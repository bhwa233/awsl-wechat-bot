"""Cortex 数据模型"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import StrEnum

from common.models import WSMessageBroadcast

logger = logging.getLogger(__name__)


class TriggerType(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    COMMAND = "command"
    CUSTOM_COMMAND = "custom_command"
    CUSTOM_COMMAND_LIST = "custom_command_list"
    COMMAND_REFRESH = "command_refresh"
    AI = "ai"


@dataclass(frozen=True)
class CommandMatch:
    key: str
    params: str = ""


@dataclass(frozen=True, kw_only=True)
class TriggerTask:
    trigger_type: TriggerType
    group_name: str
    content: str = ""
    params: str = ""


class MessageListener(ABC):
    """Gateway 消息监听器基类

    子类只需实现 _handle_message,异常由基类统一捕获.
    """

    def on_message(self, msg: WSMessageBroadcast) -> None:
        try:
            self._handle_message(msg)
        except Exception:
            logger.exception(f"{self.__class__.__name__} 处理消息异常")

    @abstractmethod
    def _handle_message(self, msg: WSMessageBroadcast) -> None: ...
