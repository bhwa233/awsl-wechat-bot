"""Bot 子进程管理器"""

import logging
import os
import socket
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx

from common.config import config

logger = logging.getLogger("daemon")

PROJECT_DIR = Path(__file__).parent.parent.resolve()
BOT_ENTRY = PROJECT_DIR / "main.py"
BOT_HEALTH_URL = f"http://127.0.0.1:{config.HTTP_API_PORT}/api/health"

HEALTH_CHECK_INTERVAL = 60
HEALTH_CHECK_TIMEOUT = 10
HEALTH_CHECK_MAX_FAILURES = 3


class BotProcessManager:
    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._log_buffer: deque = deque(maxlen=config.DAEMON_LOG_BUFFER_LINES)
        self._lock = threading.Lock()
        self._start_time: Optional[datetime] = None
        self._restart_count = 0
        self._last_restart_reason = ""
        self._auto_restart = True
        self._monitor_thread: Optional[threading.Thread] = None
        self._health_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._health_failures = 0

    def start_bot(self, reason: str = "manual") -> dict:
        with self._lock:
            if self._process and self._process.poll() is None:
                logger.warning("start_bot 被调用但 bot 已在运行中")
                return {"ok": False, "message": "bot 已在运行中"}
            self._launch(reason)
            logger.info(f"bot 已启动 (pid={self._process.pid}, reason={reason})")
            return {"ok": True, "message": "bot 已启动", "pid": self._process.pid}

    def stop_bot(self) -> dict:
        with self._lock:
            self._auto_restart = False
            result = self._terminate()
            logger.info(f"bot 已停止: {result['message']}")
            return result

    def restart_bot(self, reason: str = "manual") -> dict:
        with self._lock:
            self._terminate()
            self._launch(reason)
            self._restart_count += 1
            logger.info(f"bot 已重启 (pid={self._process.pid}, reason={reason})")
            return {"ok": True, "message": "bot 已重启", "pid": self._process.pid}

    def get_status(self) -> dict:
        with self._lock:
            running = self._process is not None and self._process.poll() is None
            return {
                "running": running,
                "pid": self._process.pid if running else None,
                "uptime_seconds": (
                    (datetime.now(timezone.utc) - self._start_time).total_seconds()
                    if running and self._start_time
                    else None
                ),
                "restart_count": self._restart_count,
                "last_restart_reason": self._last_restart_reason,
                "auto_restart": self._auto_restart,
                "health_failures": self._health_failures,
            }

    def tail_log(self, lines: int = 100) -> str:
        with self._lock:
            buf = list(self._log_buffer)
        return "".join(buf[-lines:])

    def enable_auto_restart(self):
        self._auto_restart = True

    def shutdown(self):
        logger.info("daemon 正在关闭...")
        self._stop_event.set()
        with self._lock:
            self._auto_restart = False
            self._terminate()

    # -- 内部方法 --

    def _launch(self, reason: str):
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        popen_kwargs = dict(
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(PROJECT_DIR),
            env=env,
        )
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        self._process = subprocess.Popen(
            ["uv", "run", "python", "-u", str(BOT_ENTRY)],
            **popen_kwargs,
        )
        threading.Thread(
            target=self._pipe_reader,
            args=(self._process,),
            daemon=True,
        ).start()
        self._start_time = datetime.now(timezone.utc)
        self._last_restart_reason = reason
        self._auto_restart = True
        self._health_failures = 0
        if self._monitor_thread is None or not self._monitor_thread.is_alive():
            self._stop_event.clear()
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop, daemon=True
            )
            self._monitor_thread.start()
        if self._health_thread is None or not self._health_thread.is_alive():
            self._health_thread = threading.Thread(
                target=self._health_check_loop, daemon=True
            )
            self._health_thread.start()

    def _pipe_reader(self, process: subprocess.Popen):
        try:
            for line in iter(process.stdout.readline, b""):
                if not line:
                    break
                try:
                    decoded = line.decode("utf-8", errors="replace")
                    sys.stdout.write(decoded)
                    sys.stdout.flush()
                    with self._lock:
                        self._log_buffer.append(decoded)
                except Exception:
                    pass
        finally:
            try:
                process.stdout.close()
            except Exception:
                pass

    def _terminate(self) -> dict:
        if not self._process:
            return {"ok": True, "message": "bot 未在运行"}
        pid = self._process.pid
        try:
            if sys.platform == "win32":
                self._kill_win32()
            else:
                self._kill_posix()
            if not self._wait_ports_released():
                logger.warning("bot 进程退出后端口仍未释放 (ports=%s,%s,%s)", config.HTTP_API_PORT, config.CORTEX_HTTP_PORT, config.CHATLOG_HTTP_PORT)
        except Exception:
            pass
        self._process = None
        self._start_time = None
        return {"ok": True, "message": f"bot 已停止 (pid={pid})"}

    def _kill_win32(self):
        subprocess.run(
            ["taskkill", "/PID", str(self._process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=3)

    def _kill_posix(self):
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=3)

    def _monitor_loop(self):
        consecutive_failures = 0
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=2)
            if self._stop_event.is_set():
                break
            with self._lock:
                if not self._auto_restart or self._process is None:
                    continue
                if self._process.poll() is None:
                    consecutive_failures = 0
                    continue
                exit_code = self._process.returncode
                if consecutive_failures >= config.DAEMON_MAX_RETRIES:
                    self._auto_restart = False
                    logger.error(f"bot 连续崩溃 {consecutive_failures} 次，已停止自动重启")
                    continue
                consecutive_failures += 1
                self._restart_count += 1
                reason = f"崩溃自动重启 (exit_code={exit_code}, attempt={consecutive_failures})"
                self._last_restart_reason = reason
                logger.warning(f"bot 进程退出 (exit_code={exit_code})，{config.DAEMON_RESTART_DELAY}s 后重启 (attempt={consecutive_failures}/{config.DAEMON_MAX_RETRIES})")
            time.sleep(config.DAEMON_RESTART_DELAY)
            with self._lock:
                if not self._auto_restart or self._stop_event.is_set():
                    continue
                self._launch(reason)

    def _health_check_loop(self):
        self._stop_event.wait(timeout=30)
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=HEALTH_CHECK_INTERVAL)
            if self._stop_event.is_set():
                break
            with self._lock:
                if not self._auto_restart:
                    continue
                if self._process is None or self._process.poll() is not None:
                    continue
            healthy = self._ping_health()
            with self._lock:
                if not self._auto_restart or self._process is None:
                    continue
                if healthy:
                    if self._health_failures > 0:
                        logger.info(f"bot health check 恢复正常 (之前连续失败 {self._health_failures} 次)")
                    self._health_failures = 0
                else:
                    self._health_failures += 1
                    logger.warning(f"bot health check 失败 ({self._health_failures}/{HEALTH_CHECK_MAX_FAILURES})")
                    if self._health_failures >= HEALTH_CHECK_MAX_FAILURES:
                        logger.error(f"bot 连续 {HEALTH_CHECK_MAX_FAILURES} 次 health check 失败，强制重启")
                        self._terminate()
                        self._restart_count += 1
                        reason = f"health check 失败重启 (连续 {HEALTH_CHECK_MAX_FAILURES} 次无响应)"
                        self._last_restart_reason = reason
                        self._launch(reason)

    def _ping_health(self) -> bool:
        try:
            resp = httpx.get(BOT_HEALTH_URL, timeout=HEALTH_CHECK_TIMEOUT)
            return resp.status_code == 200
        except Exception:
            return False

    def _wait_ports_released(self, timeout: float = 8.0) -> bool:
        ports = (config.HTTP_API_PORT, config.CORTEX_HTTP_PORT, config.CHATLOG_HTTP_PORT)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not any(self._is_port_open(port) for port in ports):
                return True
            time.sleep(0.2)
        return not any(self._is_port_open(port) for port in ports)

    def _is_port_open(self, port: int) -> bool:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.2)
        try:
            return sock.connect_ex(("127.0.0.1", port)) == 0
        finally:
            sock.close()
