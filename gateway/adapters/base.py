from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

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

    def messages_to_check(self, messages: list[str]) -> list[str]:
        """返回本轮需要检查的新消息候选范围"""
        return messages[-3:] if len(messages) > 3 else messages

    def can_trigger_message(self, messages_to_check: list[str], index: int) -> bool:
        """判断候选消息是否允许触发"""
        return index == len(messages_to_check) - 1

    @abstractmethod
    def find_all_wechat_windows(self) -> list[DiscoveredWindow]:
        """查找所有微信聊天窗口."""
        pass

    @abstractmethod
    def get_messages_from_window(self, window: Any) -> list[str]:
        """从指定窗口获取消息列表"""
        pass

    @abstractmethod
    def send_text_to_window(self, window: Any, text: str) -> bool:
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
        """将 MP3 文件作为语音发送,当前仅 Windows 实现"""
        return False
