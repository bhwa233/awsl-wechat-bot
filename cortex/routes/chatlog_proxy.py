"""代理 Chatlog API - 供 Cortex 前端调用，避免跨端口请求"""

import json
import logging

import httpx
from fastapi import APIRouter, Depends, Request, Response

from common.auth import verify_token

logger = logging.getLogger(__name__)


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

    @router.get("/api/chatlog/groups", dependencies=[Depends(verify_token)])
    async def proxy_chatlog_groups(request: Request):
        resp = await client.get("/api/chatlog/groups", params=dict(request.query_params))
        return _proxy_response(resp)

    @router.get("/api/chatlog/messages", dependencies=[Depends(verify_token)])
    async def proxy_chatlog_messages(request: Request):
        resp = await client.get("/api/chatlog/messages", params=dict(request.query_params))
        return _proxy_response(resp)

    @router.post("/api/chatlog/decrypt", dependencies=[Depends(verify_token)])
    async def proxy_chatlog_decrypt():
        resp = await client.post("/api/chatlog/decrypt", timeout=300)
        return _proxy_response(resp)

    @router.get("/api/chatlog/group-display-name", dependencies=[Depends(verify_token)])
    async def proxy_chatlog_group_display_name(request: Request):
        resp = await client.get("/api/chatlog/group-display-name", params=dict(request.query_params))
        return _proxy_response(resp)

    return router, client
