import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

import main as entry


class ServiceSupervisorTest(unittest.TestCase):
    def test_each_service_restarts_without_interrupting_other_services(self):
        modules = ('gateway.main', 'chatlog.main', 'cortex.main')
        processes = {module: [] for module in modules}
        stop, spawn = threading.Event(), subprocess.Popen
        code = """
import http.server, sys
from pathlib import Path
class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
    def log_message(self, *args):
        pass
server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
Path(sys.argv[1]).write_text(str(server.server_address[1]))
server.serve_forever()
"""
        with tempfile.TemporaryDirectory() as directory:
            def spawn_child(args, **kwargs):
                module = args[-1]
                port = Path(directory) / f'{module}-{len(processes[module])}'
                child = spawn([sys.executable, '-c', code, str(port)], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, **kwargs)
                processes[module].append((child, port))
                return child

            def wait_ready(module, count):
                deadline = time.monotonic() + 6
                while (len(processes[module]) < count or not processes[module][-1][1].exists()
                       or not processes[module][-1][1].stat().st_size):
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.01)

            with patch('main.subprocess.Popen', spawn_child), httpx.Client(timeout=2, trust_env=False) as client:
                threads = [threading.Thread(target=entry.supervise_service, args=(module, stop)) for module in modules]
                for thread in threads:
                    thread.start()
                try:
                    for module in modules:
                        wait_ready(module, 1)
                    for target in modules:
                        before = {module: processes[module][-1][0].pid for module in modules}
                        processes[target][-1][0].kill()
                        wait_ready(target, 2)
                        self.assertNotEqual(processes[target][-1][0].pid, before[target])
                        for module in modules:
                            child, port = processes[module][-1]
                            if module != target:
                                self.assertEqual(child.pid, before[module])
                            self.assertEqual(client.get(f'http://127.0.0.1:{port.read_text()}').status_code, 200)
                finally:
                    stop.set()
                    for thread in threads:
                        thread.join(8)
                        self.assertFalse(thread.is_alive())
                self.assertTrue(all(child.returncode is not None for history in processes.values() for child, _ in history))

    def test_stop_during_spawn_reaps_the_created_child(self):
        stop, started, release = threading.Event(), threading.Event(), threading.Event()
        processes, spawn = [], subprocess.Popen

        def delayed_spawn(args, **kwargs):
            self.assertEqual(args[-1], 'cortex.main')
            child = spawn([sys.executable, '-c', 'import time; time.sleep(60)'], **kwargs)
            processes.append(child)
            started.set()
            release.wait(3)
            return child

        with patch('main.subprocess.Popen', delayed_spawn):
            thread = threading.Thread(target=entry.supervise_service, args=('cortex.main', stop))
            thread.start()
            try:
                self.assertTrue(started.wait(3))
            finally:
                stop.set()
                release.set()
                thread.join(8)
            self.assertFalse(thread.is_alive())
            self.assertIsNotNone(processes[0].returncode)

    def test_start_failure_retries_and_stop_interrupts_backoff(self):
        stop = threading.Event()
        with patch('main.subprocess.Popen', side_effect=OSError('spawn failed')) as spawn:
            thread = threading.Thread(target=entry.supervise_service, args=('gateway.main', stop))
            thread.start()
            try:
                deadline = time.monotonic() + 3
                while spawn.call_count < 2:
                    self.assertLess(time.monotonic(), deadline)
                    time.sleep(.01)
            finally:
                stop.set()
                thread.join(.5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(spawn.call_count, 2)
