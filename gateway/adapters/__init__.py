import logging
import sys

from gateway.adapters.base import BaseWeChatAdapter

logger = logging.getLogger(__name__)


def get_wechat_adapter() -> BaseWeChatAdapter:
    """根据操作系统返回对应的微信适配器"""
    if sys.platform == "darwin":
        from gateway.adapters.macos import MacOSWeChatAdapter
        return MacOSWeChatAdapter()
    elif sys.platform == "win32":
        try:
            from gateway.adapters.windows import WindowsWeChatAdapter
            return WindowsWeChatAdapter()
        except ImportError:
            logger.error("未找到 Windows 适配器依赖,请确保已安装 uiautomation 库")
            raise
    else:
        raise NotImplementedError(f"目前不支持该操作系统: {sys.platform}")
