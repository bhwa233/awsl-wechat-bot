"""Gateway Prometheus 指标。"""

from common.metrics import ServiceMetrics


class GatewayMetrics(ServiceMetrics):
    def __init__(self) -> None:
        super().__init__("gateway")
        self._define_gauge("groups_active", "Active WeChat group detectors.")
        self._define_gauge("websocket_connections", "Connected WebSocket clients.")
        self._define_gauge("websocket_subscriptions", "Subscribed WebSocket clients.")
        self._define_gauge("message_queue_depth", "Messages waiting for WebSocket broadcast.")
        self._define_gauge("send_queue_depth", "Messages waiting to be sent to WeChat.")
        self._define_counter(
            "detected_messages_total",
            "Detected WeChat messages by processing result.",
            ("result",),
        )
        self._define_counter(
            "broadcast_messages_total",
            "Gateway messages broadcast by result.",
            ("result",),
        )
        self._define_counter("broadcast_deliveries_total", "WebSocket message deliveries scheduled.")
        self._define_counter(
            "send_enqueue_total",
            "WeChat send tasks submitted by message type and result.",
            ("message_type", "result"),
        )
        self._define_counter(
            "sends_total",
            "WeChat send tasks processed by message type.",
            ("message_type",),
        )
        self._define_histogram(
            "send_duration_seconds",
            "WeChat send task processing duration by message type.",
            ("message_type",),
            buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
        )
        self._define_counter(
            "voice_synthesis_runs_total",
            "Successful voice synthesis runs.",
        )
        self._define_histogram(
            "voice_synthesis_duration_seconds",
            "Voice synthesis duration.",
            buckets=(0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 180),
        )
        self._define_counter("detector_errors_total", "Message detector errors.")
        self._define_counter(
            "window_scans_total",
            "WeChat window discovery scans by result.",
            ("result",),
        )


metrics = GatewayMetrics()
