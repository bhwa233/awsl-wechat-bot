"""Daemon HTTP API 路由"""

import logging
import os
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Depends, Query
from fastapi.responses import HTMLResponse, PlainTextResponse

from common.auth import verify_token
from common.config import config
from .manager import BotProcessManager

logger = logging.getLogger("daemon")

PROJECT_DIR = Path(__file__).parent.parent.resolve()
PYTHON = sys.executable
DAEMON_HTML = Path(__file__).parent / "templates" / "index.html"

manager = BotProcessManager()


@asynccontextmanager
async def lifespan(application: FastAPI):
    manager.start_bot(reason="daemon startup")
    yield
    manager.shutdown()


app = FastAPI(title="AWSL Daemon Controller", version="1.0.0", lifespan=lifespan)


@app.get("/", response_class=HTMLResponse)
def index():
    return DAEMON_HTML.read_text(encoding="utf-8")


@app.get("/health")
def health():
    return {"ok": True, "auth_enabled": bool(config.HTTP_API_TOKEN)}


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
    logger.info("开始执行 /update: 停止 bot, git pull, pip install")
    manager.stop_bot()

    results = {}

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
        daemon_updated = any("daemon/" in f for f in changed_files)
    except Exception:
        pass

    results["daemon_updated"] = daemon_updated

    if daemon_updated:
        results["message"] = "daemon 已更新，daemon 将退出，请手动重启"
        logger.info("daemon 自身被更新，即将退出")

        def _delayed_exit():
            time.sleep(1)
            os._exit(0)

        threading.Thread(target=_delayed_exit, daemon=True).start()
        return results

    manager.enable_auto_restart()
    start_result = manager.start_bot(reason="API /update")
    results["bot_start"] = start_result
    results["message"] = "更新完成，bot 已重新启动"
    logger.info("更新完成，bot 已重新启动")
    return results


@app.get("/logs", dependencies=[Depends(verify_token)], response_class=PlainTextResponse)
def logs(lines: int = Query(default=100, ge=1, le=10000)):
    return manager.tail_log(lines)
