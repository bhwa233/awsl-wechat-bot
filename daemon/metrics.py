"""Daemon Prometheus 指标。"""

from common.metrics import ServiceMetrics


class DaemonMetrics(ServiceMetrics):
    def __init__(self) -> None:
        super().__init__("daemon")
        self._define_gauge("bot_running", "Whether the managed bot process is running.")
        self._define_gauge("bot_uptime_seconds", "Managed bot process uptime in seconds.")
        self._define_counter(
            "bot_restarts_total",
            "Managed bot restarts by reason.",
            ("reason",),
        )
        self._define_counter(
            "bot_health_checks_total",
            "Managed bot health checks by result.",
            ("result",),
        )
        self._define_gauge(
            "bot_health_consecutive_failures",
            "Current consecutive managed bot health check failures.",
        )
        self._define_counter(
            "cleanup_runs_total",
            "WeChat log cleanup runs by source, mode and result.",
            ("source", "dry_run", "result"),
        )
        self._define_histogram(
            "cleanup_duration_seconds",
            "WeChat log cleanup duration.",
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60),
        )
        self._define_counter(
            "cleanup_files_deleted_total",
            "WeChat log files deleted.",
        )
        self._define_counter(
            "cleanup_bytes_deleted_total",
            "WeChat log bytes deleted.",
        )


metrics = DaemonMetrics()
