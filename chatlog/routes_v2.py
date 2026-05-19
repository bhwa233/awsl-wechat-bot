"""Chatlog v2 HTTP 路由 - 原生库后端"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from chatlog.media.route import V2MediaBackendBinding, register_media_route
from chatlog.models import ChatlogGroupResponse, ChatlogMessageResponse
from chatlog.platform import get_chatlog_platform
from chatlog.service_v2 import ChatlogServiceV2
from common.auth import verify_token
from common.config import config

logger = logging.getLogger(__name__)


def create_v2_router() -> APIRouter:
    router = APIRouter()
    chatlog = ChatlogServiceV2()

    def _platform_or_501():
        try:
            return get_chatlog_platform()
        except RuntimeError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc

    def _ensure_ready() -> None:
        detail = _platform_or_501().v2_config_error(config)
        if detail:
            raise HTTPException(status_code=400, detail=detail)

    @router.post("/decrypt", dependencies=[Depends(verify_token)])
    def decrypt_database():
        return {
            "success": True,
            "message": "v2 后端无需解密, 原生库直接读取加密数据库",
            "count": 0,
        }

    @router.get(
        "/groups",
        response_model=list[ChatlogGroupResponse],
        dependencies=[Depends(verify_token)],
    )
    def list_groups(limit: int = Query(0, ge=0, le=10000, description="限制返回数量, 0 表示不限制")):
        _ensure_ready()
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

    @router.get(
        "/messages",
        response_model=list[ChatlogMessageResponse],
        dependencies=[Depends(verify_token)],
    )
    def query_messages(
        group: str = Query(..., description="群聊ID 或个人微信ID"),
        start: str | None = Query(None, description="开始时间 (YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS)"),
        end: str | None = Query(None, description="结束时间 (YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS)"),
        limit: int = Query(100, ge=1, le=10000, description="限制返回数量"),
        offset: int = Query(0, ge=0, le=100000, description="跳过前 N 条消息"),
        desc: bool = Query(False, description="是否倒序(最新消息优先)"),
    ):
        _ensure_ready()
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

    try:
        _media_binding = V2MediaBackendBinding(get_chatlog_platform(), config)
    except RuntimeError:
        # 当前平台不支持原生库, /media 端点不注册 (其他端点仍然可用并各自 501)
        _media_binding = None
    if _media_binding is not None:
        register_media_route(router, _media_binding)

    @router.get("/group-display-name", dependencies=[Depends(verify_token)])
    def get_group_display_name(group: str = Query(..., description="群聊ID")):
        _ensure_ready()
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

    @router.get("/health")
    async def health():
        if _media_binding is None:
            return {
                "status": "unavailable",
                "backend": "v2",
                "native_library": None,
                "media": V2MediaBackendBinding.unavailable_media_health(),
                "detail": "当前平台不支持 chatlog v2",
            }
        detail = _media_binding.config_error()
        ready = detail is None
        resp = {
            "status": "ok" if ready else "unavailable",
            "backend": "v2",
            "native_library": _media_binding.native_library_path(),
            "media": _media_binding.media_health(),
        }
        if detail:
            resp["detail"] = detail
        return resp

    return router
