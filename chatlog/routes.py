"""聊天记录 HTTP 路由 - 独立 chatlog 服务"""

import logging
import os
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends, Query

from chatlog.models import ChatlogGroupResponse, ChatlogMessageResponse
from chatlog.service import ChatlogService
from common.auth import verify_token
from common.config import config

logger = logging.getLogger(__name__)


def create_routes() -> APIRouter:
    router = APIRouter(prefix="/api/chatlog", tags=["chatlog"])
    chatlog = ChatlogService()

    @router.post("/decrypt", dependencies=[Depends(verify_token)])
    def decrypt_database():
        if not chatlog.has_crypto():
            raise HTTPException(status_code=500, detail="服务器缺少 pycryptodome 依赖")
        if not config.has_wechat_db_config():
            raise HTTPException(status_code=400, detail="微信数据库配置不完整")
        if not os.path.isdir(config.WECHAT_DB_INPUT_PATH):
            raise HTTPException(status_code=400, detail=f"输入目录不存在: {config.WECHAT_DB_INPUT_PATH}")

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
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"[Chatlog] 解密失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="解密失败，请查看服务器日志")

    @router.get("/groups", response_model=List[ChatlogGroupResponse], dependencies=[Depends(verify_token)])
    def list_chat_groups(limit: int = Query(0, description="限制返回数量，0 表示不限制")):
        if not config.has_wechat_db_config():
            raise HTTPException(status_code=400, detail="微信数据库配置不完整")
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
                )
                for g in groups
            ]
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            logger.error(f"[Chatlog] 列出群聊失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="查询失败，请查看服务器日志")

    @router.get("/messages", response_model=List[ChatlogMessageResponse], dependencies=[Depends(verify_token)])
    def query_messages(
        group: str = Query(..., description="群聊ID 或个人微信ID"),
        start: Optional[str] = Query(None, description="开始时间 (YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS)"),
        end: Optional[str] = Query(None, description="结束时间 (YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS)"),
        limit: int = Query(100, description="限制返回数量"),
    ):
        if not config.has_wechat_db_config():
            raise HTTPException(status_code=400, detail="微信数据库配置不完整")
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
            raise HTTPException(status_code=400, detail=f"无效的时间格式: {e}")

        try:
            messages = chatlog.get_messages(
                config.WECHAT_DB_OUTPUT_PATH,
                group,
                start_time=start_time,
                end_time=end_time,
                text_only=True,
                limit=limit,
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
                )
                for m in messages
            ]
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            logger.error(f"[Chatlog] 查询消息失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="查询失败，请查看服务器日志")

    @router.get("/group-display-name", dependencies=[Depends(verify_token)])
    def get_group_display_name(group: str = Query(..., description="群聊ID")):
        if not config.has_wechat_db_config():
            raise HTTPException(status_code=400, detail="微信数据库配置不完整")
        if not os.path.isdir(config.WECHAT_DB_OUTPUT_PATH):
            raise HTTPException(status_code=400, detail=f"目录不存在: {config.WECHAT_DB_OUTPUT_PATH}")
        try:
            display_name = chatlog.get_group_display_name(config.WECHAT_DB_OUTPUT_PATH, group)
            return {"group": group, "display_name": display_name or group}
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            logger.error(f"[Chatlog] 获取群名失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="查询失败")

    @router.get("/health")
    async def health():
        return {"status": "ok"}

    return router
