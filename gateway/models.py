"""
HTTP API 数据模型
"""

from pydantic import BaseModel, SerializeAsAny

from common.models import OutgoingMessage

# ============================================================
# 消息相关
# ============================================================

class SendMessageRequest(OutgoingMessage):
    """发送消息请求"""
    pass


class SendMessageResponse(BaseModel):
    """发送消息响应"""
    success: bool
    message: str


class GroupDebugInfo(BaseModel):
    """平台相关的窗口调试信息."""


class GroupInfo(BaseModel):
    """群组信息"""

    name: str
    active: bool
    debug_info: SerializeAsAny[GroupDebugInfo] | None = None
