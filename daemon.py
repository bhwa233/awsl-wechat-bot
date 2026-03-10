#!/usr/bin/env python3
"""
AWSL WeChat Bot - Daemon 控制器
管理 bot 子进程，提供远程控制 HTTP API。
由 Servy (Windows 服务管理器) 管理 daemon 进程本身。

架构:
    Servy (Windows Service)
      └─ daemon.py (port 8001, 常驻 HTTP API)
           └─ main.py (bot 子进程, port 8000)
"""

import hmac
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException, Depends, Query
from fastapi.responses import HTMLResponse, PlainTextResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials

from config import config

# ---------------------------------------------------------------------------
# 配置 (复用 config.py，daemon 专属配置从环境变量读取)
# ---------------------------------------------------------------------------
DAEMON_PORT = config.DAEMON_PORT
DAEMON_HOST = config.DAEMON_HOST
HTTP_API_TOKEN = config.HTTP_API_TOKEN
AUTO_RESTART_DELAY = config.DAEMON_RESTART_DELAY
MAX_RESTART_RETRIES = config.DAEMON_MAX_RETRIES
LOG_BUFFER_LINES = config.DAEMON_LOG_BUFFER_LINES
HEALTH_CHECK_INTERVAL = 60  # 每 60 秒检查一次
HEALTH_CHECK_TIMEOUT = 10   # 请求超时秒数
HEALTH_CHECK_MAX_FAILURES = 3  # 连续失败几次判定不健康
BOT_HEALTH_URL = f"http://127.0.0.1:{config.HTTP_API_PORT}/api/health"

PROJECT_DIR = Path(__file__).parent.resolve()
PYTHON = sys.executable  # 复用启动 daemon 的同一个 Python 解释器
BOT_ENTRY = PROJECT_DIR / "main.py"
BOT_LOG_FILE = PROJECT_DIR / "bot.log"
DAEMON_HTML = PROJECT_DIR / "daemon_templates" / "index.html"

# ---------------------------------------------------------------------------
# Logging (daemon 自身日志，仅输出到 stdout)
# ---------------------------------------------------------------------------
logger = logging.getLogger("daemon")
logger.setLevel(logging.INFO)
_formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(_formatter)
logger.addHandler(_stream_handler)

# ---------------------------------------------------------------------------
# Auth (内联复制，不导入 src/)
# ---------------------------------------------------------------------------
security = HTTPBearer(auto_error=False)


def verify_token(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
) -> bool:
    if not HTTP_API_TOKEN:
        return True
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="未提供认证信息",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not hmac.compare_digest(credentials.credentials, HTTP_API_TOKEN):
        raise HTTPException(
            status_code=401,
            detail="无效的 Token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return True


# ---------------------------------------------------------------------------
# BotProcessManager
# ---------------------------------------------------------------------------
class BotProcessManager:
    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._log_buffer: deque = deque(maxlen=LOG_BUFFER_LINES)
        self._lock = threading.Lock()
        self._start_time: Optional[datetime] = None
        self._restart_count = 0
        self._last_restart_reason = ""
        self._auto_restart = True
        self._monitor_thread: Optional[threading.Thread] = None
        self._health_thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._health_failures = 0

    # -- 公开方法 --

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
            self._last_restart_reason = reason
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

    def flush_log_to_file(self):
        """把 buffer 内容覆盖写入 bot.log 备份"""
        with self._lock:
            buf = list(self._log_buffer)
        try:
            with open(BOT_LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(buf)
        except Exception:
            pass

    def enable_auto_restart(self):
        self._auto_restart = True

    def shutdown(self):
        logger.info("daemon 正在关闭...")
        self._stop_event.set()
        with self._lock:
            self._auto_restart = False
            self._terminate()
        self.flush_log_to_file()

    # -- 内部方法 --

    def _launch(self, reason: str):
        popen_kwargs = dict(
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=str(PROJECT_DIR),
        )
        if sys.platform == "win32":
            popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        self._process = subprocess.Popen(
            [PYTHON, "-u", str(BOT_ENTRY)],
            **popen_kwargs,
        )
        # 日志读取线程：从 PIPE 读，写入内存 buffer
        threading.Thread(
            target=self._pipe_reader,
            args=(self._process,),
            daemon=True,
        ).start()
        self._start_time = datetime.now(timezone.utc)
        self._last_restart_reason = reason
        self._auto_restart = True
        self._health_failures = 0
        # 确保监控线程在运行
        if self._monitor_thread is None or not self._monitor_thread.is_alive():
            self._stop_event.clear()
            self._monitor_thread = threading.Thread(
                target=self._monitor_loop, daemon=True
            )
            self._monitor_thread.start()
        # 确保 health check 线程在运行
        if self._health_thread is None or not self._health_thread.is_alive():
            self._health_thread = threading.Thread(
                target=self._health_check_loop, daemon=True
            )
            self._health_thread.start()

    def _pipe_reader(self, process: subprocess.Popen):
        """从子进程 PIPE 逐行读取，写入内存 buffer"""
        try:
            for line in iter(process.stdout.readline, b""):
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace")
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
                # 阶梯式停止: CTRL_BREAK -> terminate -> kill
                try:
                    os.kill(self._process.pid, signal.CTRL_BREAK_EVENT)
                    self._process.wait(timeout=5)
                except (subprocess.TimeoutExpired, OSError):
                    self._process.terminate()
                    try:
                        self._process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self._process.kill()
                        self._process.wait(timeout=3)
            else:
                self._process.terminate()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self._process.kill()
                    self._process.wait(timeout=3)
        except Exception:
            pass
        self._process = None
        self._start_time = None
        self.flush_log_to_file()
        return {"ok": True, "message": f"bot 已停止 (pid={pid})"}

    def _monitor_loop(self):
        consecutive_failures = 0
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=2)
            if self._stop_event.is_set():
                break
            with self._lock:
                if not self._auto_restart:
                    continue
                if self._process is None:
                    continue
                if self._process.poll() is None:
                    # 进程还活着，重置失败计数
                    consecutive_failures = 0
                    continue
                # 进程已退出
                exit_code = self._process.returncode
                if consecutive_failures >= MAX_RESTART_RETRIES:
                    self._auto_restart = False
                    logger.error(f"bot 连续崩溃 {consecutive_failures} 次，已停止自动重启")
                    continue
                consecutive_failures += 1
                self._restart_count += 1
                reason = f"崩溃自动重启 (exit_code={exit_code}, attempt={consecutive_failures})"
                self._last_restart_reason = reason
                logger.warning(f"bot 进程退出 (exit_code={exit_code})，{AUTO_RESTART_DELAY}s 后重启 (attempt={consecutive_failures}/{MAX_RESTART_RETRIES})")
                # 等一下再重启
            time.sleep(AUTO_RESTART_DELAY)
            with self._lock:
                if not self._auto_restart or self._stop_event.is_set():
                    continue
                self._launch(reason)

    def _health_check_loop(self):
        # 启动后等一会儿再开始检查，给 bot 启动时间
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
            # 在锁外面做 HTTP 请求，避免阻塞其他操作
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

            # 每分钟顺便把 buffer 刷到文件备份
            self.flush_log_to_file()

    def _ping_health(self) -> bool:
        try:
            resp = httpx.get(BOT_HEALTH_URL, timeout=HEALTH_CHECK_TIMEOUT)
            return resp.status_code == 200
        except Exception:
            return False



# ---------------------------------------------------------------------------
# FastAPI 应用
# ---------------------------------------------------------------------------
manager = BotProcessManager()


@asynccontextmanager
async def lifespan(application: FastAPI):
    yield
    manager.shutdown()


app = FastAPI(title="AWSL Daemon Controller", version="1.0.0", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
def index():
    return DAEMON_HTML.read_text(encoding="utf-8")


@app.get("/status", dependencies=[Depends(verify_token)])
def status():
    return manager.get_status()


@app.post("/start", dependencies=[Depends(verify_token)])
def start():
    manager.enable_auto_restart()
    return manager.start_bot(reason="API /start")


@app.post("/stop", dependencies=[Depends(verify_token)])
def stop():
    return manager.stop_bot()


@app.post("/restart", dependencies=[Depends(verify_token)])
def restart():
    return manager.restart_bot(reason="API /restart")


@app.post("/update", dependencies=[Depends(verify_token)])
def update():
    # 1. 停止 bot
    logger.info("开始执行 /update: 停止 bot, git pull, pip install")
    manager.stop_bot()

    results = {}

    # 2. git pull
    try:
        git_result = subprocess.run(
            ["git", "pull"],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=60,
        )
        results["git_pull"] = {
            "stdout": git_result.stdout,
            "stderr": git_result.stderr,
            "returncode": git_result.returncode,
        }
    except Exception as e:
        results["git_pull"] = {"error": str(e)}

    # 3. pip install
    try:
        pip_result = subprocess.run(
            [PYTHON, "-m", "pip", "install", "-r", "requirements.txt"],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=300,
        )
        results["pip_install"] = {
            "stdout": pip_result.stdout,
            "stderr": pip_result.stderr,
            "returncode": pip_result.returncode,
        }
    except Exception as e:
        results["pip_install"] = {"error": str(e)}

    # 4. 检查 daemon.py 自身是否被更新
    daemon_updated = False
    try:
        diff_result = subprocess.run(
            ["git", "diff", "ORIG_HEAD", "HEAD", "--name-only"],
            cwd=str(PROJECT_DIR),
            capture_output=True,
            text=True,
            timeout=10,
        )
        changed_files = diff_result.stdout.strip().split("\n")
        daemon_updated = "daemon.py" in changed_files
    except Exception:
        pass

    results["daemon_updated"] = daemon_updated

    if daemon_updated:
        # daemon.py 自身被更新，需要退出让 Servy 重启
        results["message"] = "daemon.py 已更新，daemon 将退出并由 Servy 重启"
        logger.info("daemon.py 自身被更新，即将退出让 Servy 重启")

        def _delayed_exit():
            time.sleep(1)
            os._exit(0)

        threading.Thread(target=_delayed_exit, daemon=True).start()
        return results

    # 5. 重新启动 bot
    manager.enable_auto_restart()
    start_result = manager.start_bot(reason="API /update")
    results["bot_start"] = start_result
    results["message"] = "更新完成，bot 已重新启动"
    logger.info("更新完成，bot 已重新启动")
    return results


@app.get("/logs", dependencies=[Depends(verify_token)], response_class=PlainTextResponse)
def logs(lines: int = Query(default=100, ge=1, le=10000)):
    return manager.tail_log(lines)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info(f"daemon 启动中 (host={DAEMON_HOST}, port={DAEMON_PORT})")
    manager.start_bot(reason="daemon startup")
    uvicorn.run(app, host=DAEMON_HOST, port=DAEMON_PORT)
