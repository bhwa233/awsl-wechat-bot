"""公共 logging 工具"""

import logging
import re

_NOISY_ACCESS_RE = re.compile(
    r'"(?:GET|HEAD) '
    r'(?:/health|/api/health|/status|/logs(?:\?[^ ]*)?|/|/manifest\.json|/[^/ ?]+\.(?:css|js|map|ico|png|svg|woff2?))'
    r'(?:\?[^ ]*)? '
)


class HealthFilter(logging.Filter):
    """过滤高频轮询端点的 access log(health/status/logs/index)"""

    def filter(self, record: logging.LogRecord) -> bool:
        return not _NOISY_ACCESS_RE.search(record.getMessage())


def setup_uvicorn_logging():
    """给 uvicorn.access 挂 filter.在 FastAPI lifespan startup 中调用,
    确保在 uvicorn 完成 configure_logging 之后生效(幂等)."""
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, HealthFilter) for f in logger.filters):
        logger.addFilter(HealthFilter())
