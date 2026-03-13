"""
聊天记录数据模型 - HTTP API 响应模型
"""

from typing import List
from pydantic import BaseModel


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
