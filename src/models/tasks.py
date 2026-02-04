"""消息和任务数据模型"""
from dataclasses import dataclass
from enum import Enum
from typing import Any


class TriggerType(str, Enum):
    """触发类型枚举"""
    TEXT = "text"
    IMAGE = "image"
    COMMAND = "command"
    COMMAND_REFRESH = "command_refresh"
    AI = "ai"


@dataclass
class MessageTask:
    """待处理的消息任务"""
    trigger_type: TriggerType
    group_name: str
    window: Any
    content: Any
    original_message: str
    timestamp: float


@dataclass
class SendTask:
    """待发送的消息"""
    group_name: str
    content: str
    is_image: bool = False
