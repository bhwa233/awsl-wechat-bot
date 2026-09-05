"""
健康检查和首页路由
"""

import time
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse

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
        groups_count = len(gateway_app.groups)
        healthy = groups_count > 0
        content = {
            "service": "gateway",
            "status": "healthy" if healthy else "unhealthy",
            "groups_count": groups_count,
            "server_time": datetime.now().isoformat(),
            "timezone": time.strftime("%Z"),
            "timezone_offset": time.strftime("%z"),
            "auth_enabled": bool(config.HTTP_API_TOKEN),
            "capabilities": {
                "voice": gateway_app.adapter.SUPPORTS_VOICE,
                "max_binary_file_bytes": config.BINARY_MAX_FILE_BYTES,
            },
        }
        return JSONResponse(content=content, status_code=200 if healthy else 503)

    return router
