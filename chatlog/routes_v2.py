"""Chatlog v2 HTTP 路由 - 原生库后端"""

import logging
import threading
import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse

from chatlog.media.route import V2MediaBackendBinding, register_media_route
from chatlog.models import (
    ChatlogBatchMessagesRequest,
    ChatlogBatchMessagesResponse,
    ChatlogContactResponse,
    ChatlogGroupMemberResponse,
    ChatlogGroupResponse,
    ChatlogMessageResponse,
    ChatlogMessageSearchRequest,
)
from chatlog.platform import get_chatlog_platform
from chatlog.search import normalize_search_filters, parse_time_range
from chatlog.service_v2 import ChatlogServiceV2
from common.auth import verify_token, verify_token_or_query
from common.config import config

logger = logging.getLogger(__name__)


# wechat_running 是全进程扫描, 高频轮询场景下缓存几秒足够
_WECHAT_RUNNING_TTL = 3.0


def create_v2_router() -> APIRouter:
    chatlog = ChatlogServiceV2()
    running_cache_lock = threading.Lock()
    running_cache: list = [0.0, True]  # [过期时间, 结果]

    def _platform_or_501():
        try:
            return get_chatlog_platform()
        except RuntimeError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc

    def _wechat_running_cached(platform) -> bool:
        now = time.time()
        with running_cache_lock:
            if running_cache[0] > now:
                return running_cache[1]
        result = platform.wechat_running()
        with running_cache_lock:
            running_cache[0] = now + _WECHAT_RUNNING_TTL
            running_cache[1] = result
        return result

    def _ensure_ready() -> None:
        platform = _platform_or_501()
        detail = platform.v2_config_error(config)
        if detail:
            raise HTTPException(status_code=400, detail=detail)
        if not _wechat_running_cached(platform):
            raise HTTPException(status_code=503, detail="微信进程未运行, 暂不可读取数据库")

    def _ensure_ready_after_auth(_authenticated: bool = Depends(verify_token)) -> None:
        _ensure_ready()

    def _ensure_ready_after_media_auth(_authenticated: bool = Depends(verify_token_or_query)) -> None:
        _ensure_ready()

    router = APIRouter()
    auth_router = APIRouter(dependencies=[Depends(verify_token)])
    db_router = APIRouter(dependencies=[Depends(_ensure_ready_after_auth)])
    media_router = APIRouter(dependencies=[Depends(_ensure_ready_after_media_auth)])

    @auth_router.post("/decrypt")
    def decrypt_database():
        return {
            "success": True,
            "message": "v2 后端无需解密, 原生库直接读取加密数据库",
            "count": 0,
        }

    @db_router.get(
        "/groups",
        response_model=list[ChatlogGroupResponse],
    )
    def list_groups(limit: int = Query(0, ge=0, le=10000, description="限制返回数量, 0 表示不限制")):
        try:
            groups = chatlog.list_groups(
                config.WECHAT_DB_INPUT_PATH,
                config.WECHAT_DB_KEY,
                limit=limit,
            )
            return [
                ChatlogGroupResponse(
                    username=g.username,
                    owner=g.owner,
                    remark=g.remark,
                    nick_name=g.nick_name,
                    display_name=g.display_name(),
                    avatar=g.avatar,
                    big_avatar=g.big_avatar,
                )
                for g in groups
            ]
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            logger.error(f"[ChatlogV2] 列出群聊失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="查询失败, 请查看服务器日志") from e

    @db_router.get(
        "/contacts",
        response_model=list[ChatlogContactResponse],
    )
    def list_contacts(usernames: str = Query(..., description="逗号分隔的 username 列表(联系人或群成员 wxid), 最多 200 个")):
        ids = [u.strip() for u in usernames.split(",") if u.strip()]
        if not ids:
            raise HTTPException(status_code=400, detail="usernames 不能为空")
        if len(ids) > 200:
            raise HTTPException(status_code=400, detail="一次最多查询 200 个 username")
        try:
            contacts = chatlog.get_contacts(
                config.WECHAT_DB_INPUT_PATH,
                config.WECHAT_DB_KEY,
                ids,
            )
            return [
                ChatlogContactResponse(
                    username=c.username,
                    nick_name=c.nick_name,
                    remark=c.remark,
                    alias=c.alias,
                    avatar=c.avatar,
                    big_avatar=c.big_avatar,
                )
                for c in contacts
            ]
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            logger.error(f"[ChatlogV2] 查询联系人失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="查询失败, 请查看服务器日志") from e

    @db_router.get(
        "/members",
        response_model=list[ChatlogGroupMemberResponse],
    )
    def list_group_members(group: str = Query(..., description="群聊ID (xxx@chatroom)")):
        if not group.endswith("@chatroom"):
            raise HTTPException(status_code=400, detail="group 必须是群聊ID (xxx@chatroom)")
        try:
            members = chatlog.get_room_members(
                config.WECHAT_DB_INPUT_PATH,
                config.WECHAT_DB_KEY,
                group,
            )
            return [
                ChatlogGroupMemberResponse(
                    username=m.username,
                    display_name=m.display_name,
                    nick_name=m.nick_name,
                    remark=m.remark,
                    alias=m.alias,
                    avatar=m.avatar,
                    big_avatar=m.big_avatar,
                    is_owner=m.is_owner,
                )
                for m in members
            ]
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            logger.error(f"[ChatlogV2] 查询群成员失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="查询失败, 请查看服务器日志") from e

    @db_router.get(
        "/messages",
        response_model=list[ChatlogMessageResponse],
    )
    def query_messages(
        group: str = Query(..., description="群聊ID 或个人微信ID"),
        start: str | None = Query(None, description="开始时间 (YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS)"),
        end: str | None = Query(None, description="结束时间 (YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS)"),
        limit: int = Query(100, ge=1, le=10000, description="限制返回数量"),
        offset: int = Query(0, ge=0, le=100000, description="跳过前 N 条消息"),
        desc: bool = Query(False, description="是否倒序(最新消息优先)"),
    ):
        start_time = end_time = None
        try:
            if start:
                fmt = "%Y-%m-%d %H:%M:%S" if " " in start else "%Y-%m-%d"
                start_time = datetime.strptime(start, fmt)
            if end:
                fmt = "%Y-%m-%d %H:%M:%S" if " " in end else "%Y-%m-%d"
                end_time = datetime.strptime(end, fmt)
                if " " not in end:
                    end_time = end_time.replace(hour=23, minute=59, second=59)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"无效的时间格式: {e}") from e

        try:
            messages = chatlog.get_messages(
                config.WECHAT_DB_INPUT_PATH,
                config.WECHAT_DB_KEY,
                group,
                start_time=start_time,
                end_time=end_time,
                text_only=False,
                limit=limit,
                offset=offset,
                desc=desc,
            )
            return [
                ChatlogMessageResponse(
                    seq=m.seq,
                    time=m.time.isoformat(),
                    talker=m.talker,
                    sender=m.sender,
                    sender_name=m.sender_name,
                    msg_type=m.msg_type,
                    content=m.content,
                    is_self=m.is_self,
                    server_id=m.server_id,
                    server_id_str=str(m.server_id),
                    local_id=m.local_id,
                    create_time=int(m.time.timestamp()),
                    at_users=m.at_users,
                )
                for m in messages
            ]
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            logger.error(f"[ChatlogV2] 查询消息失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="查询失败, 请查看服务器日志") from e

    @db_router.post(
        "/messages/search",
        response_model=list[ChatlogMessageResponse],
    )
    def search_messages(request: ChatlogMessageSearchRequest):
        try:
            query_text, senders = normalize_search_filters(request.query, request.senders)
            start_time, end_time = parse_time_range(request.start, request.end)
            messages = chatlog.search_messages(
                config.WECHAT_DB_INPUT_PATH,
                config.WECHAT_DB_KEY,
                request.group,
                query=query_text,
                senders=senders,
                start_time=start_time,
                end_time=end_time,
                limit=request.limit,
                offset=request.offset,
                desc=request.desc,
            )
            return [ChatlogMessageResponse(**message.to_dict(), server_id=message.server_id) for message in messages]
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            logger.error(f"[ChatlogV2] 搜索消息失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="搜索失败, 请查看服务器日志") from e

    @db_router.post(
        "/messages/batch",
        response_model=ChatlogBatchMessagesResponse,
    )
    def query_messages_batch(req: ChatlogBatchMessagesRequest):
        """批量增量查询: 按每群 since_seq(闭区间)拉新消息, 整批合并为单条 SQL, 供低延迟轮询."""
        try:
            results = chatlog.get_messages_since(
                config.WECHAT_DB_INPUT_PATH,
                config.WECHAT_DB_KEY,
                [(item.group, item.since_seq) for item in req.items],
                limit=req.limit,
            )
            return ChatlogBatchMessagesResponse(
                results={
                    talker: [
                        ChatlogMessageResponse(
                            seq=m.seq,
                            time=m.time.isoformat(),
                            talker=m.talker,
                            sender=m.sender,
                            sender_name=m.sender_name,
                            msg_type=m.msg_type,
                            content=m.content,
                            is_self=m.is_self,
                            server_id=m.server_id,
                            server_id_str=str(m.server_id),
                            local_id=m.local_id,
                            create_time=int(m.time.timestamp()),
                            at_users=m.at_users,
                        )
                        for m in msgs
                    ]
                    for talker, msgs in results.items()
                }
            )
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            logger.error(f"[ChatlogV2] 批量查询消息失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="查询失败, 请查看服务器日志") from e

    try:
        _media_binding = V2MediaBackendBinding(get_chatlog_platform(), config)
    except RuntimeError:
        # 当前平台不支持原生库, /media 端点不注册 (其他端点仍然可用并各自 501)
        _media_binding = None
    if _media_binding is not None:
        register_media_route(media_router, _media_binding)

    @db_router.get("/group-display-name")
    def get_group_display_name(group: str = Query(..., description="群聊ID")):
        try:
            display_name = chatlog.get_group_display_name(
                config.WECHAT_DB_INPUT_PATH,
                config.WECHAT_DB_KEY,
                group,
            )
            return {"group": group, "display_name": display_name or group}
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            logger.error(f"[ChatlogV2] 获取群名失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="查询失败") from e

    def _health():
        if _media_binding is None:
            return JSONResponse(status_code=503, content={
                "status": "unavailable",
                "backend": "v2",
                "native_library": None,
                "media": V2MediaBackendBinding.unavailable_media_health(),
                "detail": "当前平台不支持 chatlog v2",
            })
        detail = _media_binding.config_error()
        resp = {
            "status": "unavailable",
            "backend": "v2",
            "native_library": _media_binding.native_library_path(),
            "media": _media_binding.media_health(),
        }
        if detail:
            resp["detail"] = detail
            return JSONResponse(status_code=503, content=resp)

        try:
            _ensure_ready()
            chatlog.list_groups(
                config.WECHAT_DB_INPUT_PATH,
                config.WECHAT_DB_KEY,
                limit=1,
            )
        except HTTPException as exc:
            resp["detail"] = str(exc.detail)
            return JSONResponse(status_code=503, content=resp)
        except Exception as exc:
            logger.error("[ChatlogV2] 健康检查读取群聊失败: %s", exc, exc_info=True)
            resp["detail"] = "微信数据库读取失败"
            return JSONResponse(status_code=503, content=resp)

        resp["status"] = "ok"
        return resp

    @router.get("/health")
    async def health():
        return await run_in_threadpool(_health)

    router.include_router(auth_router)
    router.include_router(db_router)
    if _media_binding is not None:
        router.include_router(media_router)

    return router
