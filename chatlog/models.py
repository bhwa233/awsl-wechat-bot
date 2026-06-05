"""
聊天记录数据模型 - HTTP API 响应模型
"""

from pydantic import BaseModel, model_validator


class ChatlogGroupResponse(BaseModel):
    """群聊信息响应"""
    username: str
    owner: str
    remark: str
    nick_name: str
    display_name: str
    avatar: str = ""       # small_head_url (/132 小图), 来自 contact 表, 腾讯 CDN 直链
    big_avatar: str = ""   # big_head_url (/0 原图)


class ChatlogContactResponse(BaseModel):
    """联系人/群成员信息响应"""
    username: str
    nick_name: str = ""
    remark: str = ""
    alias: str = ""
    avatar: str = ""       # small_head_url (/132 小图)
    big_avatar: str = ""   # big_head_url (/0 原图)


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
    server_id: int = 0
    # server_id 是 64-bit 整数, 字符串版本给 JS 客户端避免 Number 精度丢失 (2^53 上限)
    server_id_str: str = ""

    @model_validator(mode="after")
    def _sync_server_id_str(self):
        self.server_id_str = str(self.server_id)
        return self
