"""群聊总结路由"""
import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from common.auth import verify_token
from cortex.services.summary_service import SummaryConfig, SummaryGroup, SummaryResult, SummaryService

logger = logging.getLogger(__name__)


class SummaryGroupRequest(BaseModel):
    group_id: str
    group_name: str


class SummaryRequest(BaseModel):
    groups: list[SummaryGroupRequest]
    date: str | None = None


class SummaryResponse(BaseModel):
    success: bool
    message: str


def create_routes(summary_service: SummaryService) -> APIRouter:
    router = APIRouter(prefix="/api", tags=["summary"])

    @router.post("/summary", response_model=SummaryResponse, dependencies=[Depends(verify_token)])
    async def send_chat_summary(request: SummaryRequest):
        if not request.groups:
            return SummaryResponse(success=False, message="groups 不能为空")

        config = SummaryConfig(
            groups=[SummaryGroup(group_id=g.group_id, group_name=g.group_name) for g in request.groups],
            date=request.date,
        )
        group_names = [g.group_name for g in request.groups]

        def on_complete(result: SummaryResult):
            if result.success:
                logger.info(f"[Summary] 任务完成: {result.message}")
            else:
                logger.error(f"[Summary] 任务失败: {result.message}")

        result = summary_service.start_summary_async(config, on_complete)
        if not result.success:
            return SummaryResponse(success=False, message=result.message)

        logger.info(f"[Summary] 已启动总结任务: {group_names}")
        return SummaryResponse(success=True, message=f"已启动群聊总结任务,目标群聊: {', '.join(group_names)}")

    return router
