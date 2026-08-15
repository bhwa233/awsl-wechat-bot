import sys

from gateway.adapters.base import BaseWeChatAdapter
from gateway.console.adapters.base import BaseConsoleAdapter


def get_console_adapter(wechat_adapter: BaseWeChatAdapter) -> BaseConsoleAdapter | None:
    """返回当前平台的 Console 适配器."""
    if sys.platform != "win32":
        return None

    from gateway.adapters.windows import WindowsWeChatAdapter
    from gateway.console.adapters.windows import WindowsConsoleAdapter

    if not isinstance(wechat_adapter, WindowsWeChatAdapter):
        raise TypeError("Windows Console 需要 WindowsWeChatAdapter")
    return WindowsConsoleAdapter(wechat_adapter)
