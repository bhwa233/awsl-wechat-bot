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
        """向指定聊天窗口发送文本、图片或文件"""
        if not gateway.send_message(request):
            raise HTTPException(status_code=503, detail="发送队列已满，请稍后重试")

        return SendMessageResponse(success=True, message="消息已加入发送队列")

    return router
