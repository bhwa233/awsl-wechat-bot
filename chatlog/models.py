"""
聊天记录数据模型 - HTTP API 响应模型
"""

from typing import Annotated

from pydantic import BaseModel, Field, model_validator


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


class ChatlogGroupMemberResponse(BaseModel):
    """群成员信息响应"""
    username: str
    display_name: str = ""   # 群内昵称 (来自 chat_room.ext_buffer)
    nick_name: str = ""      # 微信昵称 (来自 contact 表)
    remark: str = ""
    alias: str = ""
    avatar: str = ""         # small_head_url (/132 小图)
    big_avatar: str = ""     # big_head_url (/0 原图)
    is_owner: bool = False   # 是否群主


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
    local_id: int = 0
    create_time: int = 0
    # server_id 是 64-bit 整数, 字符串版本给 JS 客户端避免 Number 精度丢失 (2^53 上限)
    server_id_str: str = ""
    # 群聊被 @ 人的 wxid 列表 (来自 source 字段 zstd XML 的 <atuserlist>); 非群聊/无@时为空
    at_users: list[str] = []

    @model_validator(mode="after")
    def _sync_server_id_str(self):
        self.server_id_str = str(self.server_id)
        return self


class ChatlogMessageCountRequest(BaseModel):
    """消息查询与统计过滤条件。"""

    group: str
    query: str = Field(default="", max_length=256)
    senders: list[str] = Field(default_factory=list, max_length=200)
    start: str | None = None
    end: str | None = None
    msg_types: list[Annotated[int, Field(strict=True, ge=0, le=0xFFFFFFFF)]] = Field(
        default_factory=lambda: [1], max_length=200,
    )


    @model_validator(mode="after")
    def _validate_query_message_types(self):
        if self.query.strip() and set(self.msg_types) != {1}:
            raise ValueError("query 仅支持文字消息，msg_types 必须为 [1]")
        return self


class ChatlogMessageCountResponse(BaseModel):
    count: int
    by_sender: dict[str, int]


class ChatlogMessageSearchRequest(ChatlogMessageCountRequest):
    """消息搜索请求，默认仅文字。"""

    limit: int = Field(default=100, ge=1, le=10000)
    offset: int = Field(default=0, ge=0, le=100000)
    desc: bool = False


class ChatlogBatchItem(BaseModel):
    """批量增量查询的单个群游标"""
    group: str
    # 上次已见最大 seq(sort_seq); 闭区间 >=, 调用方自行去重边界行
    since_seq: int = Field(default=0, ge=0)


class ChatlogBatchMessagesRequest(BaseModel):
    """POST /messages/batch 请求体"""
    items: list[ChatlogBatchItem] = Field(min_length=1, max_length=200)
    # 每个群最多返回条数
    limit: int = Field(default=500, ge=1, le=10000)


class ChatlogBatchMessagesResponse(BaseModel):
    """POST /messages/batch 响应: group -> 升序消息列表"""
    results: dict[str, list[ChatlogMessageResponse]]
