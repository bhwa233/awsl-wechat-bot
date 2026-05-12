"""Chatlog 平台特性工厂."""

from __future__ import annotations

import sys
from functools import lru_cache

from chatlog.platform.base import ChatlogPlatform
from chatlog.platform.linux import LinuxChatlogPlatform
from chatlog.platform.windows import WindowsChatlogPlatform


@lru_cache(maxsize=1)
def get_chatlog_platform() -> ChatlogPlatform:
    if sys.platform.startswith("win"):
        return WindowsChatlogPlatform()
    if sys.platform.startswith("linux"):
        return LinuxChatlogPlatform()
    raise RuntimeError(f"不支持的 chatlog 平台: {sys.platform}")


def is_chatlog_platform_supported() -> bool:
    try:
        get_chatlog_platform()
    except RuntimeError:
        return False
    return True


__all__ = [
    "ChatlogPlatform",
    "LinuxChatlogPlatform",
    "WindowsChatlogPlatform",
    "get_chatlog_platform",
    "is_chatlog_platform_supported",
]
