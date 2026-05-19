"""HTTP API 认证模块"""

import hmac
from typing import Annotated

from fastapi import Depends, HTTPException, Query
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from common.config import config

security = HTTPBearer(auto_error=False)
TokenCredentials = Annotated[HTTPAuthorizationCredentials | None, Depends(security)]


def verify_token(credentials: TokenCredentials = None) -> bool:
    """
    验证 Bearer Token
    如果 HTTP_API_TOKEN 为空,则不启用认证
    """
    if not config.HTTP_API_TOKEN:
        return True

    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="未提供认证信息",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not hmac.compare_digest(credentials.credentials, config.HTTP_API_TOKEN):
        raise HTTPException(
            status_code=401,
            detail="无效的 Token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return True


def verify_token_or_query(
    credentials: TokenCredentials = None,
    token: str | None = Query(None, description="兼容浏览器 DOM 资源 (img/audio) 的 query token"),
) -> bool:
    """Header Bearer 优先, 不带 header 时回退 ?token=xxx; 用于 <img>/<audio> 等无法注入 header 的场景.

    HTTP_API_TOKEN 为空时直接 bypass (与 verify_token 一致).
    注意: query token 会进 access log / Referer / 浏览器历史, 仅用于内网/本机 cortex 场景.
    """
    if not config.HTTP_API_TOKEN:
        return True
    if credentials and hmac.compare_digest(credentials.credentials, config.HTTP_API_TOKEN):
        return True
    if token and hmac.compare_digest(token, config.HTTP_API_TOKEN):
        return True
    raise HTTPException(
        status_code=401,
        detail="认证失败 (需要 Bearer header 或 ?token= query)",
        headers={"WWW-Authenticate": "Bearer"},
    )
