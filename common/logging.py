"""公共 logging 工具"""

import logging
import re

_NOISY_ACCESS_RE = re.compile(
    r'"(?:GET|HEAD) '
    r'(?:/health|/api/health|/status|/logs(?:\?[^ ]*)?|/|/manifest\.json|/[^/ ?]+\.(?:css|js|map|ico|png|svg|woff2?))'
    r'(?:\?[^ ]*)? '
)

# query token 出现在 ?token=xxx / &token=xxx, 在 access log 里要打马赛克防泄漏
_TOKEN_QUERY_RE = re.compile(r"([?&])token=[^& \"]+")


class HealthFilter(logging.Filter):
    """过滤高频轮询端点的 access log(health/status/logs/index)"""

    def filter(self, record: logging.LogRecord) -> bool:
        return not _NOISY_ACCESS_RE.search(record.getMessage())


class TokenQueryRedactFilter(logging.Filter):
    """把 access log 里的 ?token=xxx 改成 ?token=REDACTED."""

    def filter(self, record: logging.LogRecord) -> bool:
        # uvicorn.access 的格式化 args 走 record.args, 也可能整体在 msg 里; 都改
        if record.args:
            new_args = tuple(
                _TOKEN_QUERY_RE.sub(r"\1token=REDACTED", a) if isinstance(a, str) else a
                for a in record.args
            )
            record.args = new_args
        if isinstance(record.msg, str):
            record.msg = _TOKEN_QUERY_RE.sub(r"\1token=REDACTED", record.msg)
        return True


def setup_uvicorn_logging():
    """给 uvicorn.access 挂 filter.在 FastAPI lifespan startup 中调用,
    确保在 uvicorn 完成 configure_logging 之后生效(幂等)."""
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, HealthFilter) for f in logger.filters):
        logger.addFilter(HealthFilter())
    if not any(isinstance(f, TokenQueryRedactFilter) for f in logger.filters):
        logger.addFilter(TokenQueryRedactFilter())
