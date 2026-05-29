"""Cortex 健康检查"""
from datetime import datetime

from fastapi import APIRouter

from common.config import config

router = APIRouter()


def create_routes():
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
