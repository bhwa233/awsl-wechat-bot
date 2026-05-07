"""Daemon HTTP API 路由"""

import logging
import subprocess
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse, PlainTextResponse

from common.auth import verify_token
from common.config import config
from .manager import BotProcessManager

logger = logging.getLogger("daemon")

DAEMON_HTML = Path(__file__).parent / "templates" / "index.html"
PROJECT_DIR = Path(__file__).parent.parent.resolve()

manager = BotProcessManager()
_lifecycle_lock = threading.Lock()


def _with_lifecycle_lock(op: str):
    """获取 lifecycle 锁，冲突返回 409。返回 context manager。"""
    if not _lifecycle_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=f"另一个生命周期操作正在执行，{op} 被拒绝")


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
    _with_lifecycle_lock("/start")
    try:
        manager.enable_auto_restart()
        return manager.start_bot(reason="API /start")
    finally:
        _lifecycle_lock.release()


@app.post("/stop", dependencies=[Depends(verify_token)])
def stop():
    _with_lifecycle_lock("/stop")
    try:
        return manager.stop_bot()
    finally:
        _lifecycle_lock.release()


@app.post("/restart", dependencies=[Depends(verify_token)])
def restart():
    _with_lifecycle_lock("/restart")
    try:
        return manager.restart_bot(reason="API /restart")
    finally:
        _lifecycle_lock.release()


@app.post("/update", dependencies=[Depends(verify_token)])
def update():
    _with_lifecycle_lock("/update")
    try:
        logger.info("开始执行 /update: 停止 bot, git pull, uv sync")
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
            if git_result.returncode != 0:
                results["message"] = f"git pull 失败 (returncode={git_result.returncode})，bot 未重启"
                logger.error(f"git pull 失败: {git_result.stderr}")
                raise HTTPException(status_code=500, detail=results)
        except HTTPException:
            raise
        except Exception as e:
            results["git_pull"] = {"error": str(e)}
            results["message"] = f"git pull 异常: {e}，bot 未重启"
            logger.error(f"git pull 异常: {e}")
            raise HTTPException(status_code=500, detail=results)

        try:
            uv_result = subprocess.run(
                ["uv", "sync"],
                cwd=str(PROJECT_DIR),
                capture_output=True,
                text=True,
                timeout=300,
            )
            results["uv_sync"] = {
                "stdout": uv_result.stdout,
                "stderr": uv_result.stderr,
                "returncode": uv_result.returncode,
            }
            if uv_result.returncode != 0:
                results["message"] = f"uv sync 失败 (returncode={uv_result.returncode})，bot 未重启"
                logger.error(f"uv sync 失败: {uv_result.stderr}")
                raise HTTPException(status_code=500, detail=results)
        except HTTPException:
            raise
        except Exception as e:
            results["uv_sync"] = {"error": str(e)}
            results["message"] = f"uv sync 异常: {e}，bot 未重启"
            logger.error(f"uv sync 异常: {e}")
            raise HTTPException(status_code=500, detail=results)

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

        manager.enable_auto_restart()
        start_result = manager.start_bot(reason="API /update")
        results["bot_start"] = start_result

        if daemon_updated:
            results["message"] = "更新完成，bot 已重新启动；daemon 已更新，请手动重启 daemon 以加载新代码"
            logger.info("daemon 自身已更新，bot 已重新启动，等待手动重启 daemon")
            return results

        results["message"] = "更新完成，bot 已重新启动"
        logger.info("更新完成，bot 已重新启动")
        return results
    finally:
        _lifecycle_lock.release()


@app.get("/logs", dependencies=[Depends(verify_token)], response_class=PlainTextResponse)
def logs(lines: int = Query(default=100, ge=1, le=10000)):
    return manager.tail_log(lines)


@app.post("/logs/clear", dependencies=[Depends(verify_token)])
def clear_logs():
    manager.clear_log()
    return {"ok": True, "message": "日志已清除"}
