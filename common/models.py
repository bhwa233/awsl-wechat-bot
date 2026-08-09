"""公共数据模型 - WS 协议 + 内部队列"""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, Discriminator, Field, Tag, TypeAdapter, field_validator, model_validator

from common.config import config

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


class MessageType(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    VOICE = "voice"
    VOICE_FILE = "voice_file"


class TaskType(StrEnum):
    SEND = "send"
    SUMMARY = "summary"


class MessagePayload(BaseModel):
    message_type: MessageType = MessageType.TEXT
    message: str | None = Field(None, max_length=10_000_000)
    filename: str | None = None

    @model_validator(mode="after")
    def check_content(self):
        if self.message_type in {MessageType.FILE, MessageType.VOICE_FILE} and not self.filename:
            raise ValueError(f"{self.message_type.value} 类型必须提供 filename")
        if not self.message:
            raise ValueError("message 不能为空")
        if self.message_type == MessageType.VOICE:
            if self.filename:
                raise ValueError("voice 文本类型不接受 filename, MP3 请使用 voice_file")
            if len(self.message) > config.WINDOWS_VOICE_MAX_TEXT_LENGTH:
                raise ValueError(f"voice 文本不能超过 {config.WINDOWS_VOICE_MAX_TEXT_LENGTH} 字符")
        return self


class OutgoingMessage(MessagePayload):
    group_name: str


# ── Client → Server ──────────────────────────────────────

class WSAuth(BaseModel):
    type: Literal[WSType.AUTH]
    token: str


MAX_REGEX_LEN = 200


class WSSubscribe(BaseModel):
    type: Literal[WSType.SUBSCRIBE]
    request_id: str | None = None
    group_filter: str = ""
    message_filter: str = ""
    priority: int = 0
    passive: bool = False
    client_name: str = ""

    @field_validator("group_filter", "message_filter")
    @classmethod
    def validate_regex(cls, v):
        if v:
            if len(v) > MAX_REGEX_LEN:
                raise ValueError(f"正则表达式长度不能超过 {MAX_REGEX_LEN} 字符")
            re.compile(v)
        return v


class WSUnsubscribe(BaseModel):
    type: Literal[WSType.UNSUBSCRIBE]
    request_id: str | None = None


class WSSendMessage(MessagePayload):
    type: Literal[WSType.SEND_MESSAGE]
    request_id: str | None = None
    group_name: str

    def to_outgoing_message(self) -> OutgoingMessage:
        return OutgoingMessage(
            group_name=self.group_name,
            message_type=self.message_type,
            message=self.message,
            filename=self.filename,
        )


# Discriminated union: 一步解析客户端消息
WSClientMessage = Annotated[
    Annotated[WSAuth, Tag(WSType.AUTH)]
    | Annotated[WSSubscribe, Tag(WSType.SUBSCRIBE)]
    | Annotated[WSUnsubscribe, Tag(WSType.UNSUBSCRIBE)]
    | Annotated[WSSendMessage, Tag(WSType.SEND_MESSAGE)],
    Discriminator(lambda v: v.get("type") if isinstance(v, dict) else getattr(v, "type", None)),
]
_client_message_adapter = TypeAdapter(WSClientMessage)


def parse_client_message(raw: str) -> WSAuth | WSSubscribe | WSUnsubscribe | WSSendMessage:
    """解析客户端 WS 消息(JSON → Pydantic model,含验证)

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
    Annotated[WSAuthOk, Tag(WSType.AUTH_OK)]
    | Annotated[WSSubscribeOk, Tag(WSType.SUBSCRIBE_OK)]
    | Annotated[WSMessageBroadcast, Tag(WSType.MESSAGE)]
    | Annotated[WSSendOk, Tag(WSType.SEND_OK)]
    | Annotated[WSError, Tag(WSType.ERROR)],
    Discriminator(lambda v: v.get("type") if isinstance(v, dict) else getattr(v, "type", None)),
]
_server_message_adapter = TypeAdapter(WSServerMessage)


def parse_server_message(raw: str) -> WSAuthOk | WSSubscribeOk | WSMessageBroadcast | WSSendOk | WSError:
    """解析服务端 WS 消息(JSON → Pydantic model,含验证)

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
    client_name: str = ""

    @staticmethod
    def from_subscribe(msg: "WSSubscribe") -> "WSSubscription":
        """从 WSSubscribe 构建订阅状态,regex 编译失败会抛 re.error"""
        return WSSubscription(
            subscribed=True,
            group_re=re.compile(msg.group_filter) if msg.group_filter else None,
            message_re=re.compile(msg.message_filter) if msg.message_filter else None,
            priority=msg.priority,
            passive=msg.passive,
            client_name=msg.client_name,
        )
