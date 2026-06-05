"""聊天记录 HTTP 路由 - 独立 chatlog 服务"""

import logging
import os
import threading
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query

from chatlog.media.route import V1MediaBackendBinding, register_media_route
from chatlog.models import (
    ChatlogContactResponse,
    ChatlogGroupMemberResponse,
    ChatlogGroupResponse,
    ChatlogMessageResponse,
)
from chatlog.platform import get_chatlog_platform
from chatlog.service import ChatlogService
from common.auth import verify_token
from common.config import config

logger = logging.getLogger(__name__)


def create_routes() -> APIRouter:
    router = APIRouter()
    chatlog = ChatlogService()
    decrypt_lock = threading.Lock()

    def _platform_or_501():
        try:
            return get_chatlog_platform()
        except RuntimeError as exc:
            raise HTTPException(status_code=501, detail=str(exc)) from exc

    def _ensure_v1_config() -> None:
        detail = _platform_or_501().v1_config_error(config)
        if detail:
            raise HTTPException(status_code=400, detail=detail)

    @router.post("/decrypt", dependencies=[Depends(verify_token)])
    def decrypt_database():
        if not chatlog.has_crypto():
            raise HTTPException(status_code=500, detail="服务器缺少 pycryptodome 依赖")
        _ensure_v1_config()
        if not os.path.isdir(config.WECHAT_DB_INPUT_PATH):
            raise HTTPException(status_code=400, detail=f"输入目录不存在: {config.WECHAT_DB_INPUT_PATH}")

        if not decrypt_lock.acquire(blocking=False):
            raise HTTPException(status_code=409, detail="解密任务正在进行中, 请稍后重试")
        try:
            count = chatlog.decrypt(config.WECHAT_DB_INPUT_PATH, config.WECHAT_DB_KEY, config.WECHAT_DB_OUTPUT_PATH)
            logger.info(f"[Chatlog] 解密完成: {count} 个文件")
            return {
                "success": True,
                "message": f"成功解密 {count} 个文件",
                "count": count,
                "output_path": config.WECHAT_DB_OUTPUT_PATH,
            }
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            logger.error(f"[Chatlog] 解密失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="解密失败, 请查看服务器日志") from e
        finally:
            decrypt_lock.release()

    @router.get("/groups", response_model=list[ChatlogGroupResponse], dependencies=[Depends(verify_token)])
    def list_chat_groups(limit: int = Query(0, description="限制返回数量, 0 表示不限制")):
        _ensure_v1_config()
        if not os.path.isdir(config.WECHAT_DB_OUTPUT_PATH):
            raise HTTPException(status_code=400, detail=f"目录不存在: {config.WECHAT_DB_OUTPUT_PATH}")

        try:
            groups = chatlog.list_groups(config.WECHAT_DB_OUTPUT_PATH, limit=limit)
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
        except Exception as e:
            logger.error(f"[Chatlog] 列出群聊失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="查询失败, 请查看服务器日志") from e

    @router.get("/contacts", response_model=list[ChatlogContactResponse], dependencies=[Depends(verify_token)])
    def list_contacts(usernames: str = Query(..., description="逗号分隔的 username 列表(联系人或群成员 wxid), 最多 200 个")):
        _ensure_v1_config()
        if not os.path.isdir(config.WECHAT_DB_OUTPUT_PATH):
            raise HTTPException(status_code=400, detail=f"目录不存在: {config.WECHAT_DB_OUTPUT_PATH}")
        ids = [u.strip() for u in usernames.split(",") if u.strip()]
        if not ids:
            raise HTTPException(status_code=400, detail="usernames 不能为空")
        if len(ids) > 200:
            raise HTTPException(status_code=400, detail="一次最多查询 200 个 username")
        try:
            contacts = chatlog.get_contacts(config.WECHAT_DB_OUTPUT_PATH, ids)
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
        except Exception as e:
            logger.error(f"[Chatlog] 查询联系人失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="查询失败, 请查看服务器日志") from e

    @router.get("/members", response_model=list[ChatlogGroupMemberResponse], dependencies=[Depends(verify_token)])
    def list_group_members(group: str = Query(..., description="群聊ID (xxx@chatroom)")):
        _ensure_v1_config()
        if not os.path.isdir(config.WECHAT_DB_OUTPUT_PATH):
            raise HTTPException(status_code=400, detail=f"目录不存在: {config.WECHAT_DB_OUTPUT_PATH}")
        if not group.endswith("@chatroom"):
            raise HTTPException(status_code=400, detail="group 必须是群聊ID (xxx@chatroom)")
        try:
            members = chatlog.get_room_members(config.WECHAT_DB_OUTPUT_PATH, group)
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
        except Exception as e:
            logger.error(f"[Chatlog] 查询群成员失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="查询失败, 请查看服务器日志") from e

    @router.get("/messages", response_model=list[ChatlogMessageResponse], dependencies=[Depends(verify_token)])
    def query_messages(
        group: str = Query(..., description="群聊ID 或个人微信ID"),
        start: str | None = Query(None, description="开始时间 (YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS)"),
        end: str | None = Query(None, description="结束时间 (YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS)"),
        limit: int = Query(100, ge=1, le=10000, description="限制返回数量"),
        offset: int = Query(0, ge=0, le=100000, description="跳过前 N 条消息"),
        desc: bool = Query(False, description="是否倒序(最新消息优先)"),
    ):
        _ensure_v1_config()
        if not os.path.isdir(config.WECHAT_DB_OUTPUT_PATH):
            raise HTTPException(status_code=400, detail=f"目录不存在: {config.WECHAT_DB_OUTPUT_PATH}")

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
                config.WECHAT_DB_OUTPUT_PATH,
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
        except Exception as e:
            logger.error(f"[Chatlog] 查询消息失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="查询失败, 请查看服务器日志") from e

    try:
        _media_binding = V1MediaBackendBinding(get_chatlog_platform(), config)
    except RuntimeError:
        _media_binding = None
    if _media_binding is not None:
        register_media_route(router, _media_binding)

    @router.get("/group-display-name", dependencies=[Depends(verify_token)])
    def get_group_display_name(group: str = Query(..., description="群聊ID")):
        _ensure_v1_config()
        if not os.path.isdir(config.WECHAT_DB_OUTPUT_PATH):
            raise HTTPException(status_code=400, detail=f"目录不存在: {config.WECHAT_DB_OUTPUT_PATH}")
        try:
            display_name = chatlog.get_group_display_name(config.WECHAT_DB_OUTPUT_PATH, group)
            return {"group": group, "display_name": display_name or group}
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e)) from e
        except Exception as e:
            logger.error(f"[Chatlog] 获取群名失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="查询失败") from e

    @router.get("/health")
    async def health():
        if _media_binding is None:
            return {
                "status": "unavailable",
                "backend": "v1",
                "media": V1MediaBackendBinding.unavailable_media_health(),
                "detail": "当前平台不支持 chatlog",
            }
        detail = _media_binding.config_error()
        ready = detail is None
        resp = {
            "status": "ok" if ready else "unavailable",
            "backend": "v1",
            "media": _media_binding.media_health(),
        }
        if detail:
            resp["detail"] = detail
        return resp

    return router
