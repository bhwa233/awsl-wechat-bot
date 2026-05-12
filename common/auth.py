"""HTTP API 认证模块"""

import hmac
from typing import Annotated

from fastapi import Depends, HTTPException
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
