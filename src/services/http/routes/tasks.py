"""
定时任务路由
"""

import json
import logging
from typing import List

from fastapi import APIRouter, HTTPException, Depends

from ..auth import verify_token
from ...scheduled_task import (
    ScheduledTaskCreate, ScheduledTaskUpdate,
    ScheduledTaskApiCreate, ScheduledTaskApiUpdate,
    ScheduledTaskResponse,
)

logger = logging.getLogger(__name__)


def _task_to_response(task) -> ScheduledTaskResponse:
    """将 ORM 对象转换为响应模型（target_groups: JSON str -> list）"""
    try:
        target_groups = json.loads(task.target_groups) if task.target_groups else []
    except json.JSONDecodeError:
        target_groups = []

    return ScheduledTaskResponse(
        id=task.id,
        name=task.name,
        cron_expression=task.cron_expression,
        message=task.message,
        message_type=task.message_type,
        image_base64=task.image_base64 if task.message_type == "image" else "",
        target_groups=target_groups,
        enabled=task.enabled,
        created_at=task.created_at,
        updated_at=task.updated_at,
        last_run=task.last_run,
    )


def create_routes(task_service):
    """创建路由"""
    router = APIRouter()

    @router.get("/api/tasks", response_model=List[ScheduledTaskResponse], dependencies=[Depends(verify_token)])
    async def list_scheduled_tasks():
        return [_task_to_response(t) for t in task_service.get_all_tasks()]

    @router.post("/api/tasks", response_model=ScheduledTaskResponse, dependencies=[Depends(verify_token)])
    async def create_scheduled_task(request: ScheduledTaskApiCreate):
        data = ScheduledTaskCreate(
            name=request.name,
            cron_expression=request.cron_expression,
            message=request.message or "",
            message_type=request.message_type,
            image_base64=request.image_base64 or "",
            target_groups=json.dumps(request.target_groups, ensure_ascii=False),
        )
        task = task_service.create_task(data)
        if not task:
            raise HTTPException(status_code=400, detail="创建任务失败，请检查 cron 表达式是否正确")
        logger.info(f"[HTTP API] 创建定时任务: {task.name}")
        return _task_to_response(task)

    @router.get("/api/tasks/{task_id}", response_model=ScheduledTaskResponse, dependencies=[Depends(verify_token)])
    async def get_scheduled_task(task_id: int):
        task = task_service.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"未找到任务: {task_id}")
        return _task_to_response(task)

    @router.put("/api/tasks/{task_id}", response_model=ScheduledTaskResponse, dependencies=[Depends(verify_token)])
    async def update_scheduled_task(task_id: int, request: ScheduledTaskApiUpdate):
        # 只传前端实际提交的字段，保证 exclude_unset 生效
        raw = request.model_dump(exclude_unset=True)
        if "target_groups" in raw:
            raw["target_groups"] = json.dumps(raw["target_groups"], ensure_ascii=False)
        data = ScheduledTaskUpdate(**raw)
        task = task_service.update_task(task_id, data)
        if not task:
            raise HTTPException(status_code=404, detail=f"未找到任务或 cron 表达式无效: {task_id}")
        logger.info(f"[HTTP API] 更新定时任务: {task_id}")
        return _task_to_response(task)

    @router.delete("/api/tasks/{task_id}", dependencies=[Depends(verify_token)])
    async def delete_scheduled_task(task_id: int):
        if not task_service.delete_task(task_id):
            raise HTTPException(status_code=404, detail=f"未找到任务: {task_id}")
        logger.info(f"[HTTP API] 删除定时任务: {task_id}")
        return {"success": True, "message": "任务已删除"}

    return router
