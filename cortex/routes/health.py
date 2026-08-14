"""Cortex 健康检查"""
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from common.config import config

router = APIRouter()


def create_routes(cortex_app):
    @router.get("/api/health")
    async def health():
        now = datetime.now()
        utc_offset = now.astimezone().utcoffset()
        offset_hours = int(utc_offset.total_seconds() // 3600) if utc_offset else 0
        healthy = cortex_app.gateway_client.connected
        content = {
            "service": "cortex",
            "status": "healthy" if healthy else "unhealthy",
            "auth_enabled": bool(config.HTTP_API_TOKEN),
            "server_time": now.isoformat(),
            "timezone": str(now.astimezone().tzinfo),
            "timezone_offset": f"{offset_hours:+d}",
            "gateway_state": cortex_app.gateway_client.state.value,
        }
        return JSONResponse(content=content, status_code=200 if healthy else 503)

    return router
