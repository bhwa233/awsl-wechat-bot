"""Prometheus 指标基础设施。"""

import logging
import threading
import time
from contextlib import suppress
from http import HTTPStatus

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from starlette.middleware import Middleware

from common.auth import verify_token

logger = logging.getLogger(__name__)

HTTP_LATENCY_BUCKETS = (
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.25,
    0.5,
    1.0,
    2.5,
    5.0,
    10.0,
    30.0,
    60.0,
    180.0,
    300.0,
)
HTTP_METHOD_LABELS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "CONNECT", "TRACE"})
HTTP_STATUS_LABELS = frozenset(str(status.value) for status in HTTPStatus)


class ServiceMetrics:
    """单个服务独享的 Registry 和 HTTP 指标。"""

    def __init__(self, service: str) -> None:
        self.registry = CollectorRegistry()
        self._instruments: dict[str, object] = {}
        self._failed_metrics: set[str] = set()
        self._failure_lock = threading.Lock()
        self._metric_kwargs = {
            "namespace": "awsl",
            "subsystem": service,
            "registry": self.registry,
        }
        self._define_counter(
            "http_requests_total",
            "HTTP requests completed by method, route and status code.",
            ("method", "route", "status"),
        )
        self._define_histogram(
            "http_request_duration_seconds",
            "HTTP request duration by method, route and status code.",
            ("method", "route", "status"),
            buckets=HTTP_LATENCY_BUCKETS,
        )
        self._define_gauge(
            "http_requests_in_progress",
            "HTTP requests currently in progress by method.",
            ("method",),
        )
        self.middleware = Middleware(PrometheusMiddleware, metrics=self)
        self.router = APIRouter()
        self.router.add_api_route(
            "/metrics",
            self._render,
            methods=["GET"],
            include_in_schema=False,
            dependencies=[Depends(verify_token)],
        )

    def _render(self) -> Response:
        return Response(generate_latest(self.registry), media_type=CONTENT_TYPE_LATEST)

    def _define_metric(self, metric_type, name: str, documentation: str, labelnames=(), **kwargs) -> None:
        try:
            metric = metric_type(
                name,
                documentation,
                labelnames,
                **kwargs,
                **self._metric_kwargs,
            )
        except Exception:
            self._report_failure(name)
            return
        self._instruments[name] = metric

    def _report_failure(self, name: str) -> None:
        with self._failure_lock:
            if name in self._failed_metrics:
                return
            self._failed_metrics.add(name)
        with suppress(Exception):
            logger.warning("Prometheus 指标 %s 操作失败,已忽略且不再重复记录日志", name, exc_info=True)

    def _record(self, name: str, operation: str, *args, **labels) -> None:
        try:
            metric = self._instruments[name]
            target = metric.labels(**labels) if labels else metric
            getattr(target, operation)(*args)
        except Exception:
            self._report_failure(name)

    def increment(self, name: str, amount: float = 1, **labels) -> None:
        self._record(name, "inc", amount, **labels)

    def decrement(self, name: str, amount: float = 1, **labels) -> None:
        self._record(name, "dec", amount, **labels)

    def set_value(self, name: str, value: float, **labels) -> None:
        self._record(name, "set", value, **labels)

    def observe(self, name: str, value: float, **labels) -> None:
        self._record(name, "observe", value, **labels)

    def bind(self, name: str, callback, **labels) -> None:
        def safe_callback():
            try:
                return float(callback())
            except Exception:
                self._report_failure(name)
                return 0

        self._record(name, "set_function", safe_callback, **labels)

    def _define_counter(self, name: str, documentation: str, labelnames=()) -> None:
        self._define_metric(Counter, name, documentation, labelnames)

    def _define_gauge(self, name: str, documentation: str, labelnames=()) -> None:
        self._define_metric(Gauge, name, documentation, labelnames)

    def _define_histogram(self, name: str, documentation: str, labelnames=(), **kwargs) -> None:
        self._define_metric(Histogram, name, documentation, labelnames, **kwargs)


class PrometheusMiddleware:
    """按路由模板聚合 HTTP 指标,避免原始 URL 造成高基数。"""

    def __init__(self, app, metrics: ServiceMetrics) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or scope.get("path") == "/metrics":
            await self.app(scope, receive, send)
            return

        raw_method = str(scope.get("method", "")).upper()
        method = raw_method if raw_method in HTTP_METHOD_LABELS else "OTHER"
        status_code = 500
        started = time.perf_counter()
        self.metrics.increment("http_requests_in_progress", method=method)

        async def send_wrapper(message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            route = getattr(scope.get("route"), "path", "__unmatched__")
            raw_status = str(status_code)
            status = raw_status if raw_status in HTTP_STATUS_LABELS else "other"
            duration = time.perf_counter() - started
            self.metrics.increment("http_requests_total", method=method, route=route, status=status)
            self.metrics.observe(
                "http_request_duration_seconds",
                duration,
                method=method,
                route=route,
                status=status,
            )
            self.metrics.decrement("http_requests_in_progress", method=method)
