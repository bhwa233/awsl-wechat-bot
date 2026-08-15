from abc import ABC, abstractmethod
from typing import Any


class BaseConsoleAdapter(ABC):
    """微信主窗口 Console 平台适配器."""

    @abstractmethod
    def get_state(self, *, handle: int | None = None, max_nodes: int = 5000) -> dict[str, Any]: ...

    @abstractmethod
    def open_chat(self, *, path: str, max_nodes: int = 5000) -> dict[str, Any]: ...

    @abstractmethod
    def scroll_chats(self, *, direction: str, max_nodes: int = 5000) -> dict[str, Any]: ...

    @abstractmethod
    def send_message(
        self,
        *,
        message_type: str,
        message: str,
        filename: str,
        chat_name: str,
        max_nodes: int = 5000,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def list_standalone_windows(self) -> dict[str, Any]: ...

    @abstractmethod
    def standalone_window_status(self, *, chat_id: str | None = None) -> dict[str, Any]: ...

    @abstractmethod
    def open_standalone_window(
        self,
        *,
        path: str,
        chat_id: str | None = None,
        max_nodes: int = 5000,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def close_standalone_window(
        self,
        *,
        window_id: int | None = None,
        chat_id: str | None = None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def cache_standalone_windows(self) -> dict[str, Any]: ...

    @abstractmethod
    def get_cached_standalone_windows(self) -> dict[str, Any]: ...

    @abstractmethod
    def open_cached_standalone_windows(self, *, max_nodes: int = 5000) -> dict[str, Any]: ...

    @abstractmethod
    def list_windows(self) -> dict[str, Any]: ...

    @abstractmethod
    def dump_window(
        self,
        *,
        handle: int,
        max_depth: int = 5,
        path: str = "",
        max_nodes: int = 2000,
        include_patterns: bool = False,
    ) -> dict[str, Any]: ...
