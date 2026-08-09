"""
消息发送路由
"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from common.auth import verify_token
from gateway.core.gateway import Gateway
from gateway.models import SendMessageRequest, SendMessageResponse

logger = logging.getLogger(__name__)
router = APIRouter()


def create_routes(gateway: Gateway):
    """创建路由"""

    @router.post("/api/send", response_model=SendMessageResponse, dependencies=[Depends(verify_token)])
    async def send_message(request: SendMessageRequest):
        """向指定聊天窗口发送文本、图片、文件、TTS 语音或 MP3 语音"""
        error = gateway.get_send_error(request)
        if error:
            raise HTTPException(status_code=501, detail=error)
        if not gateway.send_message(request):
            raise HTTPException(status_code=503, detail="发送队列已满,请稍后重试")

        return SendMessageResponse(success=True, message="消息已加入发送队列")

    return router
