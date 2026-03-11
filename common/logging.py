"""公共 logging 工具"""

import logging


class HealthFilter(logging.Filter):
    """过滤 /health 端点的 access log"""

    def filter(self, record: logging.LogRecord) -> bool:
        return "/health" not in record.getMessage()


def setup_uvicorn_logging():
    """给 uvicorn access logger 添加 health 过滤"""
    logging.getLogger("uvicorn.access").addFilter(HealthFilter())
