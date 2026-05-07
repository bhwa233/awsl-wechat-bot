"""公共 logging 工具"""

import logging
import re


# 过滤掉高频/无信息的轮询端点 access log
_NOISY_ACCESS_RE = re.compile(
    r'"(?:GET|HEAD) (?:/health|/api/health|/status|/logs(?:\?[^ ]*)?|/) '
)


class HealthFilter(logging.Filter):
    """过滤高频轮询端点的 access log（health/status/logs/index）"""

    def filter(self, record: logging.LogRecord) -> bool:
        return not _NOISY_ACCESS_RE.search(record.getMessage())


def _attach_filter():
    """给 uvicorn.access logger 挂 filter（幂等）。"""
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, HealthFilter) for f in logger.filters):
        logger.addFilter(HealthFilter())


def setup_uvicorn_logging():
    """给 uvicorn access logger 添加 health 过滤，并 hook configure_logging
    确保多 Config 实例的重新配置不会清除 filter。"""
    _attach_filter()

    try:
        import uvicorn.config as _uv_cfg
    except ImportError:
        return

    if getattr(_uv_cfg.Config.configure_logging, "_health_hooked", False):
        return

    _orig = _uv_cfg.Config.configure_logging

    def _wrapped(self):
        _orig(self)
        _attach_filter()

    _wrapped._health_hooked = True
    _uv_cfg.Config.configure_logging = _wrapped
