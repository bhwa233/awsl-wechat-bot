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
        return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")

    @router.get("/api/gateway/health", dependencies=[Depends(verify_token)])
    async def proxy_gateway_health():
        resp = await client.get("/api/health")
        return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")

    @router.post("/api/send", dependencies=[Depends(verify_token)])
    async def proxy_send(request: Request):
        body = await request.json()
        resp = await client.post("/api/send", json=body)
        return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")

    @router.api_route(
        "/api/console/{console_path:path}",
        methods=["GET", "POST"],
        dependencies=[Depends(verify_token)],
    )
    async def proxy_console(console_path: str, request: Request):
        if not console_path or any(part in {".", ".."} for part in console_path.split("/")):
            return JSONResponse(status_code=400, content={"detail": "无效 Console 路径"})

        headers = {}
        if content_type := request.headers.get("content-type"):
            headers["content-type"] = content_type
        kwargs = {
            "params": request.query_params,
            "headers": headers,
            "timeout": httpx.Timeout(connect=5.0, read=400.0, write=120.0, pool=5.0),
        }
        if body := await request.body():
            kwargs["content"] = body

        try:
            resp = await client.request(request.method, f"/api/console/{console_path}", **kwargs)
        except httpx.HTTPError as e:
            return JSONResponse(status_code=502, content={"detail": f"Gateway 请求失败: {e}"})
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers={"content-type": resp.headers.get("content-type", "application/json")},
        )

    return router, client
