"""
健康检查和首页路由
"""

import time
from datetime import datetime

from fastapi import APIRouter

from common.config import config

router = APIRouter()


def create_routes(gateway_app):
    """创建路由"""

    @router.get("/")
    async def root():
        """Gateway 首页"""
        return {"service": "gateway", "status": "running"}

    @router.get("/api/health")
    async def health():
        """健康检查"""
        return {
            "status": "healthy",
            "groups_count": len(gateway_app.groups),
            "server_time": datetime.now().isoformat(),
            "timezone": time.strftime("%Z"),
            "timezone_offset": time.strftime("%z"),
            "auth_enabled": bool(config.HTTP_API_TOKEN),
            "capabilities": {
                "voice": gateway_app.adapter.SUPPORTS_VOICE,
            },
        }

    return router
