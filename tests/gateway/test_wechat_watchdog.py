import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from gateway.startup import WeChatStartup
from gateway.startup.adapters import get_login_adapter
from gateway.startup.adapters.base import LoginSnapshot
from gateway.startup.adapters.linux import LinuxLoginAdapter
from gateway.startup.adapters.windows import WindowsLoginAdapter
from gateway.startup.restart_budget import RestartBudget


class WatchdogTests(unittest.TestCase):
    def setUp(self):
        self.worker = WeChatStartup(MagicMock(), MagicMock())
        self.worker._stop.wait = MagicMock(return_value=False)
        self.driver = MagicMock()
        self.driver.processes.return_value = [object()]
        self.driver.session_ready.return_value = True
        self.worker._restart_budget = MagicMock()

    def test_confirmed_exit_requests_restart_without_writing_empty_cache(self):
        self.driver.processes.return_value = []
        self.assertTrue(self.worker._watch_loop(self.driver))
        self.worker.console.cache_standalone_windows.assert_not_called()
        self.driver.launch.assert_not_called()
        self.assertEqual(self.driver.processes.call_count, 2)

    def test_live_unknown_window_is_not_killed_or_relaunched(self):
        self.driver.probe.return_value = LoginSnapshot('transition')
        self.worker._stop.wait.return_value = True
        self.assertFalse(self.worker._watch_loop(self.driver))
        self.driver.launch.assert_not_called()
        self.worker.console.cache_standalone_windows.assert_not_called()
        self.assertEqual(self.worker.status['state'], 'waiting')

    def test_qr_ends_watch_without_cache_or_restart(self):
        self.driver.probe.return_value = LoginSnapshot('qr_required')
        self.assertFalse(self.worker._watch_loop(self.driver))
        self.worker._restart_budget.claim.assert_not_called()
        self.worker.console.cache_standalone_windows.assert_not_called()

    def test_exit_reuses_same_login_restore_workflow(self):
        with patch('gateway.startup.get_login_adapter', return_value=self.driver), \
             patch.object(self.worker, '_login_and_restore', return_value='ready') as login, \
             patch.object(self.worker, '_watch_loop', side_effect=[True, False]):
            self.worker._run()
        self.assertEqual(self.driver.launch.call_count, 2)
        self.assertEqual(login.call_count, 2)
        self.worker._restart_budget.claim.assert_called_once()

    def test_three_restarts_then_stop(self):
        self.worker._restart_budget.claim.side_effect = [True, True, True, False]
        with patch('gateway.startup.get_login_adapter', return_value=self.driver), \
             patch.object(self.worker, '_login_and_restore', return_value='ready'), \
             patch.object(self.worker, '_watch_loop', return_value=True):
            self.worker._run()
        self.assertEqual(self.driver.launch.call_count, 4)
        self.assertEqual(self.worker.status['state'], 'restart_limited')

    def test_waits_for_desktop_without_launching(self):
        self.driver.session_ready.return_value = False
        self.worker._stop.wait.return_value = True
        with patch('gateway.startup.get_login_adapter', return_value=self.driver):
            self.worker._run()
        self.driver.launch.assert_not_called()
        self.assertEqual(self.worker.status['state'], 'waiting')

    def test_exit_during_login_requests_restart(self):
        self.driver.processes.return_value = []
        with patch('gateway.startup.time.monotonic', side_effect=[0, 11]):
            self.assertEqual(self.worker._login_and_restore(self.driver), 'restart')
        self.driver.click_login.assert_not_called()

    def test_periodic_state_checks_never_save_windows(self):
        self.driver.probe.return_value = LoginSnapshot('logged_in')
        self.worker._stop.wait.side_effect = [False] * 6 + [True]
        with patch('gateway.startup.time.monotonic', side_effect=[0, 5, 10, 15, 20, 25, 30]):
            self.worker._watch_loop(self.driver)
        self.assertEqual(self.driver.processes.call_count, 7)
        self.assertEqual(self.driver.probe.call_count, 2)
        self.worker.console.cache_standalone_windows.assert_not_called()

    def test_platform_factory(self):
        for platform, adapter in [('linux', LinuxLoginAdapter), ('win32', WindowsLoginAdapter)]:
            with patch('gateway.startup.adapters.sys.platform', platform):
                self.assertIsInstance(get_login_adapter(MagicMock(), MagicMock()), adapter)

    def test_linux_unavailable_display_is_waited_for(self):
        import subprocess

        adapter = MagicMock()
        adapter._run_x11.side_effect = subprocess.TimeoutExpired('xdotool', 3)
        self.assertFalse(LinuxLoginAdapter(adapter, MagicMock()).session_ready())

    def test_windows_session_zero_does_not_launch_ui(self):
        adapter = WindowsLoginAdapter(MagicMock(), MagicMock())
        with patch.object(adapter, '_session_id', return_value=0):
            self.assertFalse(adapter.session_ready())


class RestartBudgetTests(unittest.TestCase):
    def test_budget_survives_worker_restart_and_expires(self):
        with tempfile.TemporaryDirectory() as directory, patch('gateway.startup.restart_budget.time.time', return_value=10000):
            path = Path(directory) / 'restarts.json'
            for _ in range(3):
                self.assertTrue(RestartBudget(path).claim())
            self.assertFalse(RestartBudget(path).claim())
            if sys.platform != "win32":
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with patch('gateway.startup.restart_budget.time.time', return_value=13601):
                self.assertTrue(RestartBudget(path).claim())

    def test_invalid_budget_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'restarts.json'
            for data in ({}, [True], ['bad'], [float('nan')], [1, 2, 3, 4]):
                path.write_text(json.dumps(data))
                with self.assertRaises(ValueError):
                    RestartBudget(path).claim()

    def test_clock_rollback_does_not_clear_budget(self):
        with tempfile.TemporaryDirectory() as directory, patch('gateway.startup.restart_budget.time.time', return_value=100):
            path = Path(directory) / 'restarts.json'
            path.write_text(json.dumps([1000, 1001, 1002]))
            self.assertFalse(RestartBudget(path).claim())
