"""Chatlog Prometheus 指标。"""

from chatlog.platform import get_chatlog_platform
from common.config import config
from common.metrics import ServiceMetrics


class ChatlogMetrics(ServiceMetrics):
    def __init__(self) -> None:
        super().__init__("chatlog")
        self._define_gauge(
            "backend_configured",
            "Whether the Chatlog backend configuration is valid.",
            ("backend",),
        )
        self._define_gauge("wechat_process_running", "Whether the WeChat process is running.")
        self._bind_platform_gauges()

    def _bind_platform_gauges(self) -> None:
        try:
            platform = get_chatlog_platform()
        except RuntimeError:
            self.set_value("backend_configured", 0, backend="v1")
            self.set_value("backend_configured", 0, backend="v2")
            self.set_value("wechat_process_running", 0)
            return

        self.bind(
            "backend_configured",
            lambda: int(not platform.v1_config_error(config)),
            backend="v1",
        )
        self.bind(
            "backend_configured",
            lambda: int(not platform.v2_config_error(config)),
            backend="v2",
        )
        self.bind("wechat_process_running", platform.wechat_running)


metrics = ChatlogMetrics()
