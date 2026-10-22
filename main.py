#!/usr/bin/env python3
"""
AWSL 微信机器人 - 启动 Gateway + Chatlog + Cortex
"""

import contextlib
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

from common.config import config

log_level = logging.DEBUG if config.DEBUG else logging.INFO
logging.basicConfig(level=log_level, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def supervise_service(module: str, stop: threading.Event):
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(Path(__file__).resolve().parent), env.get("PYTHONPATH"))))
    while not stop.is_set():
        try:
            with subprocess.Popen([sys.executable, "-P", "-u", "-m", module], env=env) as process:
                while process.poll() is None and not stop.wait(.2):
                    pass
                if process.poll() is None:
                    with contextlib.suppress(ProcessLookupError):
                        process.terminate()
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        with contextlib.suppress(ProcessLookupError):
                            process.kill()
                if not stop.is_set():
                    logger.error("%s 进程退出 (code=%s), 1 秒后重启", module, process.returncode)
        except OSError:
            logger.exception("%s 子进程启动失败, 1 秒后重试", module)
        stop.wait(1)


def main():
    stop = threading.Event()
    stop_requested = False

    def request_stop(*_):
        nonlocal stop_requested
        stop_requested = True

    signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        signals.append(signal.SIGBREAK)
    handlers = {sig: signal.signal(sig, request_stop) for sig in signals}
    threads = []
    try:
        logger.info(
            "Gateway :%d + Chatlog :%d + Cortex :%d 启动中",
            config.HTTP_API_PORT, config.CHATLOG_HTTP_PORT, config.CORTEX_HTTP_PORT,
        )
        for module in ("gateway.main", "chatlog.main", "cortex.main"):
            if stop_requested:
                break
            thread = threading.Thread(target=supervise_service, args=(module, stop), daemon=True, name=module)
            thread.start()
            threads.append(thread)
        while not stop_requested:
            time.sleep(.2)
    finally:
        stop.set()
        for thread in threads:
            thread.join()
        for sig, handler in handlers.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    main()
