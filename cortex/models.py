"""Cortex 数据模型"""
import logging
from abc import ABC, abstractmethod
from enum import StrEnum

from common.models import WSMessageBroadcast

logger = logging.getLogger(__name__)


class TriggerType(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    COMMAND = "command"
    CUSTOM_COMMAND = "custom_command"
    COMMAND_REFRESH = "command_refresh"
    AI = "ai"


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
