"""代理 Chatlog API - 供 Cortex 前端调用，避免跨端口请求"""

import json
import logging
import time

import httpx
from fastapi import APIRouter, Depends, Request, Response

from common.auth import verify_token

logger = logging.getLogger(__name__)

GROUPS_CACHE_TTL = 300


def _proxy_response(resp: httpx.Response) -> Response:
    if resp.status_code >= 500:
        logger.error(f"[ChatlogProxy] 上游返回 {resp.status_code}: {resp.text[:200]}")
        body = json.dumps({"detail": "chatlog 服务内部错误"})
        return Response(content=body, status_code=502, media_type="application/json")
    return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")


def create_routes(chatlog_base_url: str, chatlog_token: str) -> tuple[APIRouter, httpx.AsyncClient]:
    """创建代理路由，返回 (router, client)，调用方负责关闭 client"""
    router = APIRouter()
    client = httpx.AsyncClient(
        base_url=chatlog_base_url.rstrip("/"),
        headers={"Authorization": f"Bearer {chatlog_token}"} if chatlog_token else {},
        timeout=httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0),
    )

    groups_cache: dict[str, tuple[float, bytes, int]] = {}

    @router.get("/api/chatlog/groups", dependencies=[Depends(verify_token)])
    async def proxy_chatlog_groups(request: Request):
        cache_key = str(sorted(request.query_params.items()))
        cached = groups_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < GROUPS_CACHE_TTL:
            return Response(content=cached[1], status_code=cached[2], media_type="application/json")
        resp = await client.get("/api/chatlog/groups", params=dict(request.query_params))
        if resp.status_code == 200:
            groups_cache[cache_key] = (time.monotonic(), resp.content, resp.status_code)
        return _proxy_response(resp)

    @router.get("/api/chatlog/messages", dependencies=[Depends(verify_token)])
    async def proxy_chatlog_messages(request: Request):
        resp = await client.get("/api/chatlog/messages", params=dict(request.query_params))
        return _proxy_response(resp)

    @router.post("/api/chatlog/decrypt", dependencies=[Depends(verify_token)])
    async def proxy_chatlog_decrypt():
        resp = await client.post("/api/chatlog/decrypt", timeout=300)
        if resp.status_code == 200:
            groups_cache.clear()
        return _proxy_response(resp)

    @router.get("/api/chatlog/group-display-name", dependencies=[Depends(verify_token)])
    async def proxy_chatlog_group_display_name(request: Request):
        resp = await client.get("/api/chatlog/group-display-name", params=dict(request.query_params))
        return _proxy_response(resp)

    @router.get("/api/chatlog/health")
    async def proxy_chatlog_health():
        resp = await client.get("/api/chatlog/health")
        return _proxy_response(resp)

    return router, client
