"""
聊天记录查询路由
"""

import logging
import os
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Depends, Query

from common.config import config
from common.auth import verify_token
from chatlog import (
    ChatlogService,
    ChatlogGroupResponse, ChatlogMessageResponse,
    ChatSummaryRequest, ChatSummaryResponse,
    SummaryConfig, SummaryGroup, SummaryResult, GroupSummaryOutput,
)
from gateway.core.gateway import Gateway

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chatlog", tags=["chatlog"])


def create_routes(gateway: Gateway):
    """创建路由

    Args:
        gateway: Gateway 实例，用于发送消息
    """
    chatlog = ChatlogService()

    @router.post("/decrypt", dependencies=[Depends(verify_token)])
    async def decrypt_database():
        """解密微信数据库（使用全局配置）"""
        if not chatlog.has_crypto():
            raise HTTPException(status_code=500, detail="服务器缺少 pycryptodome 依赖")

        if not config.has_wechat_db_config():
            raise HTTPException(status_code=400, detail="微信数据库配置不完整")

        if not os.path.isdir(config.WECHAT_DB_INPUT_PATH):
            raise HTTPException(status_code=400, detail=f"输入目录不存在: {config.WECHAT_DB_INPUT_PATH}")

        try:
            count = chatlog.decrypt(config.WECHAT_DB_INPUT_PATH, config.WECHAT_DB_KEY, config.WECHAT_DB_OUTPUT_PATH)
            logger.info(f"[HTTP API] 解密完成: {count} 个文件")
            return {
                "success": True,
                "message": f"成功解密 {count} 个文件",
                "count": count,
                "output_path": config.WECHAT_DB_OUTPUT_PATH
            }
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error(f"[HTTP API] 解密失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="解密失败，请查看服务器日志")

    @router.get("/groups", response_model=List[ChatlogGroupResponse], dependencies=[Depends(verify_token)])
    async def list_chat_groups(
        limit: int = Query(0, description="限制返回数量，0 表示不限制")
    ):
        """列出所有群聊"""
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
                    display_name=g.display_name()
                )
                for g in groups
            ]
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            logger.error(f"[HTTP API] 列出群聊失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="查询失败，请查看服务器日志")

    @router.get("/messages", response_model=List[ChatlogMessageResponse], dependencies=[Depends(verify_token)])
    async def query_messages(
        group: str = Query(..., description="群聊ID 或个人微信ID"),
        start: Optional[str] = Query(None, description="开始时间 (YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS)"),
        end: Optional[str] = Query(None, description="结束时间 (YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS)"),
        limit: int = Query(100, description="限制返回数量")
    ):
        """查询聊天记录"""
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
                    is_self=m.is_self
                )
                for m in messages
            ]
        except FileNotFoundError as e:
            raise HTTPException(status_code=404, detail=str(e))
        except Exception as e:
            logger.error(f"[HTTP API] 查询消息失败: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="查询失败，请查看服务器日志")

    @router.post("/summary", response_model=ChatSummaryResponse, dependencies=[Depends(verify_token)])
    async def send_chat_summary(request: ChatSummaryRequest):
        """
        发送群聊总结

        异步启动解密和总结任务。同一时间只允许一个总结任务运行。
        """
        # 校验必要配置
        if not config.has_wechat_db_config():
            raise HTTPException(status_code=400, detail="微信数据库配置不完整")

        def on_group_complete(output: GroupSummaryOutput):
            """每个群聊总结完成后，通过 Gateway 发送消息"""
            if not output.success:
                return
            if output.image_base64:
                if not gateway.send_message(output.group_name, output.image_base64, is_image=True):
                    logger.warning(f"[HTTP API] 总结图片入队失败，发送队列已满: {output.group_name}")
            if output.text_message:
                if not gateway.send_message(output.group_name, output.text_message, is_image=False):
                    logger.warning(f"[HTTP API] 总结文本入队失败，发送队列已满: {output.group_name}")

        # 构建配置（使用全局 config 中的路径）
        summary_config = SummaryConfig(
            input_path=config.WECHAT_DB_INPUT_PATH,
            key=config.WECHAT_DB_KEY,
            output_path=config.WECHAT_DB_OUTPUT_PATH,
            groups=[SummaryGroup(group_id=g.group_id, group_name=g.group_name) for g in request.groups],
            on_group_complete=on_group_complete,
        )

        group_names = [g.group_name for g in request.groups]

        # 完成回调
        def on_complete(result: SummaryResult):
            if result.success:
                logger.info(f"[HTTP API] 群聊总结任务完成: {result.message}")
            else:
                logger.error(f"[HTTP API] 群聊总结任务失败: {result.message}")

        # 异步启动任务（内部已处理锁和线程）
        result = chatlog.start_summary_async(summary_config, on_complete)

        if not result.success:
            return ChatSummaryResponse(
                success=False,
                message=result.message
            )

        logger.info(f"[HTTP API] 已启动群聊总结任务: {group_names}")
        return ChatSummaryResponse(
            success=True,
            message=f"已启动群聊总结任务，目标群聊: {', '.join(group_names)}"
        )

    return router
