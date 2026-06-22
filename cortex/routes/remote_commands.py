"""远程命令路由."""

import logging

from fastapi import APIRouter, Depends, HTTPException

from common.auth import verify_token
from cortex.services.command import CommandService, RemoteCommandPolicyResponse, RemoteCommandPolicyUpdate

logger = logging.getLogger(__name__)


def create_routes(command_service: CommandService | None):
    router = APIRouter()

    def _service() -> CommandService:
        if not command_service:
            raise HTTPException(status_code=503, detail="远程命令服务未启用")
        return command_service

    @router.get("/api/remote-commands/policy", response_model=RemoteCommandPolicyResponse, dependencies=[Depends(verify_token)])
    def get_policy():
        return _service().get_policy()

    @router.put("/api/remote-commands/policy", response_model=RemoteCommandPolicyResponse, dependencies=[Depends(verify_token)])
    def update_policy(payload: RemoteCommandPolicyUpdate):
        policy = _service().update_policy(payload)
        logger.info("[HTTP API] 更新远程命令白名单策略")
        return policy

    @router.post("/api/remote-commands/policy/reload", response_model=RemoteCommandPolicyResponse, dependencies=[Depends(verify_token)])
    def reload_policy():
        return _service().reload_policy()

    return router
