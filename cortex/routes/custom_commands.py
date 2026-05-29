"""自定义命令路由"""

import logging

from fastapi import APIRouter, Depends, HTTPException

from common.auth import verify_token
from cortex.services.custom_command import (
    CustomCommandCreate,
    CustomCommandResponse,
    CustomCommandService,
    CustomCommandUpdate,
)

logger = logging.getLogger(__name__)


def create_routes(custom_command_service: CustomCommandService):
    router = APIRouter()

    @router.get("/api/custom-commands", response_model=list[CustomCommandResponse], dependencies=[Depends(verify_token)])
    def list_custom_commands():
        return [custom_command_service.to_response(command) for command in custom_command_service.get_all()]

    @router.post("/api/custom-commands", response_model=CustomCommandResponse, dependencies=[Depends(verify_token)])
    def create_custom_command(payload: CustomCommandCreate):
        command = custom_command_service.create(payload)
        if not command:
            raise HTTPException(status_code=400, detail="创建自定义命令失败,请检查 key 是否重复或内容是否合法")
        logger.info(f"[HTTP API] 创建自定义命令: {command.key}")
        return custom_command_service.to_response(command)

    @router.get("/api/custom-commands/{command_id}", response_model=CustomCommandResponse, dependencies=[Depends(verify_token)])
    def get_custom_command(command_id: int):
        command = custom_command_service.get(command_id)
        if not command:
            raise HTTPException(status_code=404, detail=f"未找到自定义命令: {command_id}")
        return custom_command_service.to_response(command)

    @router.put("/api/custom-commands/{command_id}", response_model=CustomCommandResponse, dependencies=[Depends(verify_token)])
    def update_custom_command(command_id: int, payload: CustomCommandUpdate):
        command = custom_command_service.update(command_id, payload)
        if not command:
            raise HTTPException(status_code=404, detail=f"未找到自定义命令或内容不合法: {command_id}")
        logger.info(f"[HTTP API] 更新自定义命令: {command_id}")
        return custom_command_service.to_response(command)

    @router.delete("/api/custom-commands/{command_id}", dependencies=[Depends(verify_token)])
    def delete_custom_command(command_id: int):
        if not custom_command_service.delete(command_id):
            raise HTTPException(status_code=404, detail=f"未找到自定义命令: {command_id}")
        logger.info(f"[HTTP API] 删除自定义命令: {command_id}")
        return {"success": True, "message": "自定义命令已删除"}

    return router
