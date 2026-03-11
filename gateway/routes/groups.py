"""群组路由"""
from fastapi import APIRouter, Depends

from common.auth import verify_token
from gateway.models import GroupInfo

router = APIRouter()


def create_routes(runtime):
    @router.get("/api/groups", response_model=list[GroupInfo], dependencies=[Depends(verify_token)])
    async def list_groups():
        """列出所有监听中的群聊"""
        return [GroupInfo(name=name, active=True) for name in runtime.groups]

    return router
