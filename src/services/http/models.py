"""
HTTP API 数据模型
"""

from typing import Optional, List
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


# ============================================================
# 聊天记录相关
# ============================================================

class ChatlogGroupResponse(BaseModel):
    """群聊信息响应"""
    username: str
    owner: str
    remark: str
    nick_name: str
    display_name: str


class ChatlogMessageResponse(BaseModel):
    """消息响应"""
    seq: int
    time: str
    talker: str
    sender: str
    sender_name: str
    msg_type: int
    content: str
    is_self: bool


class ChatSummaryGroup(BaseModel):
    """群聊总结群组配置"""
    group_id: str
    group_name: str


class ChatSummaryRequest(BaseModel):
    """发送群聊总结请求"""
    groups: List[ChatSummaryGroup]


class ChatSummaryResponse(BaseModel):
    """发送群聊总结响应"""
    success: bool
    message: str
