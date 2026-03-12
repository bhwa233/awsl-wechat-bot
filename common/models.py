"""公共数据模型 - WS 协议 + 内部队列"""

import re
from dataclasses import dataclass
from typing import Annotated, Literal, Union

from pydantic import BaseModel, Discriminator, Tag, TypeAdapter, field_validator, model_validator


# ── 消息类型常量 ──────────────────────────────────────────

class WSType:
    """WS 消息类型常量"""
    # Client → Server
    AUTH = "auth"
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    SEND_MESSAGE = "send_message"
    # Server → Client
    AUTH_OK = "auth_ok"
    SUBSCRIBE_OK = "subscribe_ok"
    MESSAGE = "message"
    SEND_OK = "send_ok"
    ERROR = "error"


# ── Client → Server ──────────────────────────────────────

class WSAuth(BaseModel):
    type: Literal[WSType.AUTH]
    token: str


class WSSubscribe(BaseModel):
    type: Literal[WSType.SUBSCRIBE]
    request_id: str | None = None
    group_filter: str = ""
    message_filter: str = ""
    priority: int = 0
    passive: bool = False

    @field_validator("group_filter", "message_filter")
    @classmethod
    def validate_regex(cls, v):
        if v:
            re.compile(v)
        return v


class WSUnsubscribe(BaseModel):
    type: Literal[WSType.UNSUBSCRIBE]
    request_id: str | None = None


class WSSendMessage(BaseModel):
    type: Literal[WSType.SEND_MESSAGE]
    request_id: str | None = None
    group_name: str
    message: str | None = None
    image_base64: str | None = None

    @model_validator(mode="after")
    def check_content(self):
        if not self.message and not self.image_base64:
            raise ValueError("message 和 image_base64 不能同时为空")
        return self


# Discriminated union: 一步解析客户端消息
WSClientMessage = Annotated[
    Union[
        Annotated[WSAuth, Tag(WSType.AUTH)],
        Annotated[WSSubscribe, Tag(WSType.SUBSCRIBE)],
        Annotated[WSUnsubscribe, Tag(WSType.UNSUBSCRIBE)],
        Annotated[WSSendMessage, Tag(WSType.SEND_MESSAGE)],
    ],
    Discriminator(lambda v: v.get("type") if isinstance(v, dict) else getattr(v, "type", None)),
]
_client_message_adapter = TypeAdapter(WSClientMessage)


def parse_client_message(raw: str) -> WSAuth | WSSubscribe | WSUnsubscribe | WSSendMessage:
    """解析客户端 WS 消息（JSON → Pydantic model，含验证）

    Raises:
        ValueError: JSON 解析失败或 type 不合法
        ValidationError: 字段验证失败
    """
    return _client_message_adapter.validate_json(raw)


# ── Server → Client ──────────────────────────────────────

class WSAuthOk(BaseModel):
    type: Literal[WSType.AUTH_OK] = WSType.AUTH_OK


class WSSubscribeOk(BaseModel):
    type: Literal[WSType.SUBSCRIBE_OK] = WSType.SUBSCRIBE_OK
    request_id: str | None = None
    group_filter: str = ""
    message_filter: str = ""
    priority: int = 0
    passive: bool = False


class WSMessageBroadcast(BaseModel):
    type: Literal[WSType.MESSAGE] = WSType.MESSAGE
    group_name: str
    content: str
    timestamp: float


class WSSendOk(BaseModel):
    type: Literal[WSType.SEND_OK] = WSType.SEND_OK
    request_id: str | None = None


class WSError(BaseModel):
    type: Literal[WSType.ERROR] = WSType.ERROR
    request_id: str | None = None
    detail: str


# Discriminated union: 一步解析服务端消息
WSServerMessage = Annotated[
    Union[
        Annotated[WSAuthOk, Tag(WSType.AUTH_OK)],
        Annotated[WSSubscribeOk, Tag(WSType.SUBSCRIBE_OK)],
        Annotated[WSMessageBroadcast, Tag(WSType.MESSAGE)],
        Annotated[WSSendOk, Tag(WSType.SEND_OK)],
        Annotated[WSError, Tag(WSType.ERROR)],
    ],
    Discriminator(lambda v: v.get("type") if isinstance(v, dict) else getattr(v, "type", None)),
]
_server_message_adapter = TypeAdapter(WSServerMessage)


def parse_server_message(raw: str) -> WSAuthOk | WSSubscribeOk | WSMessageBroadcast | WSSendOk | WSError:
    """解析服务端 WS 消息（JSON → Pydantic model，含验证）

    Raises:
        ValueError: JSON 解析失败或 type 不合法
        ValidationError: 字段验证失败
    """
    return _server_message_adapter.validate_json(raw)


# ── 内部模型 ─────────────────────────────────────────────

class GatewayMessage(BaseModel):
    """Detector → Gateway 队列消息"""
    group_name: str
    content: str
    timestamp: float


@dataclass
class WSSubscription:
    """WS 连接订阅状态"""
    subscribed: bool = False
    group_re: re.Pattern | None = None
    message_re: re.Pattern | None = None
    priority: int = 0
    passive: bool = False
