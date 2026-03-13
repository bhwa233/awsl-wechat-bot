"""
HTTP API 数据模型
"""

from typing import Optional
from pydantic import BaseModel


# ============================================================
# 消息相关
# ============================================================

class SendMessageRequest(BaseModel):
    """发送消息请求"""
    group_name: str
    message: Optional[str] = None
    image_base64: Optional[str] = None


class SendMessageResponse(BaseModel):
    """发送消息响应"""
    success: bool
    message: str


class GroupInfo(BaseModel):
    """群组信息"""
    name: str
    active: bool
