from abc import ABC, abstractmethod
from typing import Any


class BaseWeChatAdapter(ABC):
    """微信操作适配器基类"""

    @abstractmethod
    def find_all_wechat_windows(self) -> list[dict]:
        """查找所有微信窗口

        Returns:
            list[dict]: 窗口信息列表,每个字典包含:
                - title: 窗口标题(群名)
                - window: 窗口控制对象
        """
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
