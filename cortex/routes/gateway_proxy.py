"""代理 Gateway API - 供 Cortex 前端调用，避免跨端口请求"""

import httpx
from fastapi import APIRouter, Depends, Request, Response

from common.auth import verify_token


def create_routes(gateway_base_url: str, gateway_token: str) -> tuple[APIRouter, httpx.AsyncClient]:
    """创建代理路由，返回 (router, client)，调用方负责关闭 client"""
    router = APIRouter()
    client = httpx.AsyncClient(
        base_url=gateway_base_url.rstrip("/"),
        headers={"Authorization": f"Bearer {gateway_token}"} if gateway_token else {},
        timeout=httpx.Timeout(connect=5.0, read=10.0, write=10.0, pool=5.0),
    )

    @router.get("/api/groups", dependencies=[Depends(verify_token)])
    async def proxy_groups():
        resp = await client.get("/api/groups")
        return resp.json()

    @router.post("/api/send", dependencies=[Depends(verify_token)])
    async def proxy_send(request: Request):
        body = await request.json()
        resp = await client.post("/api/send", json=body)
        return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")

    return router, client
