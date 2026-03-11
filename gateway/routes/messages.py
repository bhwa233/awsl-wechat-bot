"""
消息发送路由
"""

import logging

from fastapi import APIRouter, HTTPException, Depends

from common.auth import verify_token
from gateway.models import SendMessageRequest, SendMessageResponse
from gateway.core.gateway import Gateway

logger = logging.getLogger(__name__)
router = APIRouter()


def create_routes(gateway: Gateway):
    """创建路由"""

    @router.post("/api/send", response_model=SendMessageResponse, dependencies=[Depends(verify_token)])
    async def send_message(request: SendMessageRequest):
        """向指定聊天窗口发送消息或图片"""
        if not request.message and not request.image_base64:
            raise HTTPException(status_code=400, detail="必须提供 message 或 image_base64 参数")

        is_image = bool(request.image_base64)
        content = request.image_base64 if is_image else request.message
        message_type = "图片" if is_image else "文本消息"

        gateway.send_message(request.group_name, content, is_image=is_image)

        return SendMessageResponse(success=True, message=f"{message_type}已加入发送队列")

    return router
