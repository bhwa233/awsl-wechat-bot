"""代理 Chatlog API - 供 Cortex 前端调用,避免跨端口请求"""

import json
import logging
import time
from collections import OrderedDict

import httpx
from fastapi import APIRouter, Depends, Request, Response
from starlette.background import BackgroundTask
from starlette.responses import StreamingResponse

from common.auth import verify_token, verify_token_or_query

logger = logging.getLogger(__name__)

GROUPS_CACHE_TTL = 300
GROUPS_CACHE_MAX = 64


def _proxy_response(resp: httpx.Response) -> Response:
    if resp.status_code >= 500:
        logger.error(f"[ChatlogProxy] 上游返回 {resp.status_code}: {resp.text[:200]}")
        body = json.dumps({"detail": "chatlog 服务内部错误"})
        return Response(content=body, status_code=502, media_type="application/json")
    return Response(content=resp.content, status_code=resp.status_code, media_type="application/json")


def create_routes(chatlog_base_url: str, chatlog_token: str) -> tuple[APIRouter, httpx.AsyncClient]:
    """创建代理路由,返回 (router, client),调用方负责关闭 client"""
    router = APIRouter()
    client = httpx.AsyncClient(
        base_url=chatlog_base_url.rstrip("/"),
        headers={"Authorization": f"Bearer {chatlog_token}"} if chatlog_token else {},
        timeout=httpx.Timeout(connect=5.0, read=300.0, write=10.0, pool=5.0),
    )

    groups_cache: OrderedDict[str, tuple[float, bytes, int]] = OrderedDict()

    @router.get("/api/chatlog/groups", dependencies=[Depends(verify_token)])
    async def proxy_chatlog_groups(request: Request):
        cache_key = str(sorted(request.query_params.items()))
        cached = groups_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < GROUPS_CACHE_TTL:
            groups_cache.move_to_end(cache_key)
            return Response(content=cached[1], status_code=cached[2], media_type="application/json")
        resp = await client.get("/api/chatlog/groups", params=dict(request.query_params))
        if resp.status_code == 200:
            groups_cache[cache_key] = (time.monotonic(), resp.content, resp.status_code)
            groups_cache.move_to_end(cache_key)
            while len(groups_cache) > GROUPS_CACHE_MAX:
                groups_cache.popitem(last=False)
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

    @router.get("/api/chatlog/contacts", dependencies=[Depends(verify_token)])
    async def proxy_chatlog_contacts(request: Request):
        resp = await client.get("/api/chatlog/contacts", params=dict(request.query_params))
        return _proxy_response(resp)

    @router.get("/api/chatlog/members", dependencies=[Depends(verify_token)])
    async def proxy_chatlog_members(request: Request):
        resp = await client.get("/api/chatlog/members", params=dict(request.query_params))
        return _proxy_response(resp)

    @router.get("/api/chatlog/health")
    async def proxy_chatlog_health():
        """统一 health, chatlog 服务侧按 CHATLOG_USE_V2 决定 v1/v2 行为, 前端用 media 字段判断能力."""
        resp = await client.get("/api/chatlog/health")
        return _proxy_response(resp)

    @router.get("/api/chatlog/media", dependencies=[Depends(verify_token_or_query)])
    async def proxy_chatlog_media(request: Request):
        """代理富媒体接口, 流式透传 chatlog 返回的 bytes (image/png|jpeg/...).

        走无版本路径, 由 chatlog 服务按 CHATLOG_USE_V2 选择 v1/v2 后端.
        支持 ?token=xxx query 认证 (浏览器 <img>/<audio> 无法注入 Bearer header).

        使用 client.stream + StreamingResponse 避免在 Cortex 进程整体缓冲大媒体 (sticker 上限 8MB).
        BackgroundTask(resp.aclose) 在 response 流结束后释放上游连接.
        """
        params = {k: v for k, v in request.query_params.items() if k != "token"}
        req = client.build_request("GET", "/api/chatlog/media", params=params)
        resp = await client.send(req, stream=True)
        if resp.status_code >= 500:
            body = await resp.aread()
            await resp.aclose()
            logger.error(f"[ChatlogProxy] media 上游 {resp.status_code}: {body[:200]!r}")
            return Response(
                content=json.dumps({"detail": "chatlog 服务内部错误"}),
                status_code=502, media_type="application/json",
            )
        media_type = resp.headers.get("content-type", "application/octet-stream")
        passthrough_keys = (
            "x-media-", "cache-control", "etag", "content-length",
            "last-modified", "content-disposition",
        )
        passthrough_headers = {
            k: v for k, v in resp.headers.items()
            if any(k.lower() == p or k.lower().startswith(p) for p in passthrough_keys)
        }
        return StreamingResponse(
            resp.aiter_bytes(),
            status_code=resp.status_code,
            media_type=media_type,
            headers=passthrough_headers,
            background=BackgroundTask(resp.aclose),
        )

    return router, client
