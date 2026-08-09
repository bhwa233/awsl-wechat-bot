"""代理 Gateway API - 供 Cortex 前端调用,避免跨端口请求"""

import httpx
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse

from common.auth import verify_token


def create_routes(gateway_base_url: str, gateway_token: str) -> tuple[APIRouter, httpx.AsyncClient]:
    """创建代理路由,返回 (router, client),调用方负责关闭 client"""
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

    @router.get("/api/gateway/health", dependencies=[Depends(verify_token)])
    async def proxy_gateway_health():
        resp = await client.get("/api/health")
        return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")

    @router.post("/api/send", dependencies=[Depends(verify_token)])
    async def proxy_send(request: Request):
        body = await request.json()
        resp = await client.post("/api/send", json=body)
        return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")

    @router.post("/api/console/state", dependencies=[Depends(verify_token)])
    async def proxy_console_state(request: Request):
        body = await request.json()
        try:
            resp = await client.post("/api/console/state", json=body)
        except httpx.HTTPError as e:
            return JSONResponse(status_code=502, content={"detail": f"Gateway 请求失败: {e}"})
        return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")

    @router.post("/api/console/open-chat", dependencies=[Depends(verify_token)])
    async def proxy_console_open_chat(request: Request):
        body = await request.json()
        try:
            resp = await client.post("/api/console/open-chat", json=body)
        except httpx.HTTPError as e:
            return JSONResponse(status_code=502, content={"detail": f"Gateway 请求失败: {e}"})
        return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")

    @router.post("/api/console/send-message", dependencies=[Depends(verify_token)])
    async def proxy_console_send_message(request: Request):
        body = await request.json()
        try:
            resp = await client.post(
                "/api/console/send-message",
                json=body,
                timeout=httpx.Timeout(connect=5.0, read=400.0, write=120.0, pool=5.0),
            )
        except httpx.HTTPError as e:
            return JSONResponse(status_code=502, content={"detail": f"Gateway 请求失败: {e}"})
        return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")

    @router.post("/api/console/debug/list", dependencies=[Depends(verify_token)])
    async def proxy_console_debug_list():
        try:
            resp = await client.post("/api/console/debug/list")
        except httpx.HTTPError as e:
            return JSONResponse(status_code=502, content={"detail": f"Gateway 请求失败: {e}"})
        return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")

    @router.post("/api/console/debug/inspect", dependencies=[Depends(verify_token)])
    async def proxy_console_debug_inspect(request: Request):
        body = await request.json()
        try:
            resp = await client.post("/api/console/debug/inspect", json=body)
        except httpx.HTTPError as e:
            return JSONResponse(status_code=502, content={"detail": f"Gateway 请求失败: {e}"})
        return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")

    return router, client
