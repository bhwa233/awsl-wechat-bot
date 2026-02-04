"""
消息发送路由
"""

import logging

from fastapi import APIRouter, HTTPException, Depends

from ..auth import verify_token
from ..models import SendMessageRequest, SendMessageResponse
from src.models.tasks import SendTask

logger = logging.getLogger(__name__)
router = APIRouter()


def create_routes(bot_instance):
    """创建路由"""

    @router.post("/api/send", response_model=SendMessageResponse, dependencies=[Depends(verify_token)])
    async def send_message(request: SendMessageRequest):
        """向指定聊天窗口发送消息或图片"""
        if not request.message and not request.image_base64:
            raise HTTPException(status_code=400, detail="必须提供 message 或 image_base64 参数")

        is_image = bool(request.image_base64)
        content = request.image_base64 if is_image else request.message
        message_type = "图片" if is_image else "文本消息"

        task = SendTask(
            group_name=request.group_name,
            content=content,
            is_image=is_image,
        )
        bot_instance.send(task)

        return SendMessageResponse(success=True, message=f"{message_type}已加入发送队列")

    return router
