"""公共 logging 工具"""

import logging
import re


# 过滤掉高频/无信息的轮询端点 access log
_NOISY_ACCESS_RE = re.compile(
    r'"(?:GET|HEAD) (?:/health|/status|/logs(?:\?[^ ]*)?|/) '
)


class HealthFilter(logging.Filter):
    """过滤高频轮询端点的 access log（health/status/logs/index）"""

    def filter(self, record: logging.LogRecord) -> bool:
        return not _NOISY_ACCESS_RE.search(record.getMessage())


def setup_uvicorn_logging():
    """给 uvicorn access logger 添加 health 过滤"""
    logging.getLogger("uvicorn.access").addFilter(HealthFilter())
