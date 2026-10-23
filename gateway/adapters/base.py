from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from common.models import ReceivedMessage
from gateway.models import GroupDebugInfo


@dataclass(frozen=True, slots=True)
class DiscoveredWindow:
    """平台适配器发现的聊天窗口."""

    title: str
    window: Any
    debug_info: GroupDebugInfo | None


class BaseWeChatAdapter(ABC):
    """微信操作适配器基类"""

    SUPPORTS_VOICE = False
    SUPPORTS_MENTION_ALL = False

    @abstractmethod
    def select_new_messages(
        self,
        window: Any,
        messages: list[ReceivedMessage],
        *,
        initialize: bool = False,
    ) -> list[ReceivedMessage]:
        """在平台内部去重并返回本轮可触发消息"""
        pass

    @abstractmethod
    def find_all_wechat_windows(self) -> list[DiscoveredWindow]:
        """查找所有微信聊天窗口."""
        pass

    @abstractmethod
    def get_messages_from_window(self, window: Any) -> list[ReceivedMessage]:
        """从指定窗口获取消息列表"""
        pass

    @abstractmethod
    def send_text_to_window(self, window: Any, text: str, *, mention_all: bool = False) -> bool:
        """向指定窗口发送文本"""
        pass

    @abstractmethod
    def send_image_to_window(self, window: Any, image_base64: str) -> bool:
        """向指定窗口发送图片(base64 编码)"""
        pass

    @abstractmethod
    def send_file_to_window(self, window: Any, file_base64: str, filename: str) -> bool:
        """向指定窗口发送文件(base64 编码)"""
        pass

    def send_voice_to_window(
        self,
        window: Any,
        file_base64: str,
        filename: str,
        timeout: float | None = None,
    ) -> bool:
        """将 MP3 文件作为语音发送,由支持语音的平台实现"""
        return False
