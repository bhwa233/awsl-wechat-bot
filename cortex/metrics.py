"""Cortex Prometheus 指标。"""

from common.metrics import ServiceMetrics


class CortexMetrics(ServiceMetrics):
    def __init__(self) -> None:
        super().__init__("cortex")
        self._define_gauge("gateway_connected", "Whether Cortex is subscribed to Gateway.")
        self._define_counter("gateway_reconnects_total", "Gateway WebSocket reconnect attempts.")
        self._define_counter(
            "gateway_messages_received_total",
            "Messages received from Gateway.",
        )
        self._define_counter(
            "gateway_send_attempts_total",
            "Messages submitted to Gateway by result.",
            ("result",),
        )
        self._define_gauge("trigger_queue_depth", "Trigger tasks waiting for a Cortex worker.")
        self._define_gauge("workers_busy", "Cortex workers currently processing tasks.")
        self._define_counter(
            "trigger_tasks_total",
            "Trigger tasks by type and processing result.",
            ("trigger_type", "result"),
        )
        self._define_histogram(
            "trigger_duration_seconds",
            "Trigger task processing duration by type.",
            ("trigger_type",),
            buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 180),
        )
        self._define_counter(
            "ai_requests_total",
            "AI requests by result.",
            ("result",),
        )
        self._define_histogram(
            "ai_request_duration_seconds",
            "AI request duration.",
            buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 20, 30, 60),
        )
        self._define_gauge("webhook_workers_busy", "Webhook workers currently sending requests.")
        self._define_counter(
            "webhook_enqueues_total",
            "Webhook tasks submitted by result.",
            ("result",),
        )
        self._define_counter(
            "webhook_dispatches_total",
            "Webhook dispatch tasks by result.",
            ("result",),
        )
        self._define_histogram(
            "webhook_dispatch_duration_seconds",
            "Webhook dispatch duration including payload preparation and HTTP request.",
            buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 180),
        )
        self._define_counter(
            "scheduler_tasks_total",
            "Scheduled task executions by type and result.",
            ("task_type", "result"),
        )
        self._define_gauge("summary_running", "Whether a summary task is running.")
        self._define_counter("summary_runs_total", "Summary runs by result.", ("result",))
        self._define_histogram(
            "summary_duration_seconds",
            "Summary task duration.",
            buckets=(1, 2.5, 5, 10, 30, 60, 120, 300, 600),
        )


metrics = CortexMetrics()
