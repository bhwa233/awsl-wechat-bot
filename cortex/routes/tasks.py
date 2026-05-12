"""定时任务路由"""

import json
import logging

from fastapi import APIRouter, Depends, HTTPException

from common.auth import verify_token
from cortex.services.scheduled_task import (
    ScheduledTaskApiCreate,
    ScheduledTaskApiUpdate,
    ScheduledTaskCreate,
    ScheduledTaskResponse,
    ScheduledTaskUpdate,
)

logger = logging.getLogger(__name__)


def _task_to_response(task) -> ScheduledTaskResponse:
    try:
        target_groups = json.loads(task.target_groups) if task.target_groups else []
    except json.JSONDecodeError:
        target_groups = []

    return ScheduledTaskResponse(
        id=task.id,
        name=task.name,
        cron_expression=task.cron_expression,
        task_type=task.task_type,
        message=task.message,
        message_type=task.message_type,
        filename=task.filename,
        target_groups=target_groups,
        enabled=task.enabled,
        created_at=task.created_at,
        updated_at=task.updated_at,
        last_run=task.last_run,
    )


def create_routes(task_service):
    router = APIRouter()

    @router.get("/api/tasks", response_model=list[ScheduledTaskResponse], dependencies=[Depends(verify_token)])
    def list_scheduled_tasks():
        return [_task_to_response(t) for t in task_service.get_all_tasks()]

    @router.post("/api/tasks", response_model=ScheduledTaskResponse, dependencies=[Depends(verify_token)])
    def create_scheduled_task(payload: ScheduledTaskApiCreate):
        data = ScheduledTaskCreate(
            name=payload.name,
            cron_expression=payload.cron_expression,
            task_type=payload.task_type,
            message=payload.message or "",
            message_type=payload.message_type,
            filename=payload.filename or "",
            target_groups=json.dumps(payload.target_groups, ensure_ascii=False),
        )
        task = task_service.create_task(data)
        if not task:
            raise HTTPException(status_code=400, detail="创建任务失败,请检查 cron 表达式是否正确")
        logger.info(f"[HTTP API] 创建定时任务: {task.name}")
        return _task_to_response(task)

    @router.get("/api/tasks/{task_id}", response_model=ScheduledTaskResponse, dependencies=[Depends(verify_token)])
    def get_scheduled_task(task_id: int):
        task = task_service.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"未找到任务: {task_id}")
        return _task_to_response(task)

    @router.put("/api/tasks/{task_id}", response_model=ScheduledTaskResponse, dependencies=[Depends(verify_token)])
    def update_scheduled_task(task_id: int, payload: ScheduledTaskApiUpdate):
        raw = payload.model_dump(exclude_unset=True)
        if "target_groups" in raw:
            raw["target_groups"] = json.dumps(raw["target_groups"], ensure_ascii=False)
        data = ScheduledTaskUpdate(**raw)
        task = task_service.update_task(task_id, data)
        if not task:
            raise HTTPException(status_code=404, detail=f"未找到任务或 cron 表达式无效: {task_id}")
        logger.info(f"[HTTP API] 更新定时任务: {task_id}")
        return _task_to_response(task)

    @router.delete("/api/tasks/{task_id}", dependencies=[Depends(verify_token)])
    def delete_scheduled_task(task_id: int):
        if not task_service.delete_task(task_id):
            raise HTTPException(status_code=404, detail=f"未找到任务: {task_id}")
        logger.info(f"[HTTP API] 删除定时任务: {task_id}")
        return {"success": True, "message": "任务已删除"}

    return router
