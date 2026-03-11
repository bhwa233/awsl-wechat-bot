"""Cortex 健康检查和首页"""
import os
from datetime import datetime, timezone

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

from common.config import config

router = APIRouter()


def create_routes():
    @router.get("/", response_class=HTMLResponse)
    async def root():
        template_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates', 'index.html')
        try:
            with open(template_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            return "<h1>模板文件未找到</h1>"

    @router.get("/api/health")
    async def health():
        now = datetime.now()
        utc_offset = now.astimezone().utcoffset()
        offset_hours = int(utc_offset.total_seconds() // 3600) if utc_offset else 0
        return {
            "service": "cortex",
            "status": "running",
            "auth_enabled": bool(config.HTTP_API_TOKEN),
            "server_time": now.isoformat(),
            "timezone": str(now.astimezone().tzinfo),
            "timezone_offset": f"{offset_hours:+d}",
        }

    return router
