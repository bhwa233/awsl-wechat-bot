"""Webhook 路由"""

import logging

from fastapi import APIRouter, HTTPException, Depends

from common.auth import verify_token
from cortex.services.webhook import WebhookCreate, WebhookUpdate, WebhookResponse

logger = logging.getLogger(__name__)


def create_routes(webhook_service):
    router = APIRouter()

    @router.get("/api/webhooks", response_model=list[WebhookResponse], dependencies=[Depends(verify_token)])
    def list_webhooks():
        return webhook_service.get_all()

    @router.post("/api/webhooks", response_model=WebhookResponse, dependencies=[Depends(verify_token)])
    def create_webhook(payload: WebhookCreate):
        wh = webhook_service.create(payload)
        logger.info(f"[HTTP API] 创建 webhook: {wh.name}")
        return wh

    @router.put("/api/webhooks/{webhook_id}", response_model=WebhookResponse, dependencies=[Depends(verify_token)])
    def update_webhook(webhook_id: int, payload: WebhookUpdate):
        wh = webhook_service.update(webhook_id, payload)
        if not wh:
            raise HTTPException(status_code=404, detail=f"未找到 webhook: {webhook_id}")
        logger.info(f"[HTTP API] 更新 webhook: {webhook_id}")
        return wh

    @router.delete("/api/webhooks/{webhook_id}", dependencies=[Depends(verify_token)])
    def delete_webhook(webhook_id: int):
        if not webhook_service.delete(webhook_id):
            raise HTTPException(status_code=404, detail=f"未找到 webhook: {webhook_id}")
        logger.info(f"[HTTP API] 删除 webhook: {webhook_id}")
        return {"success": True, "message": "Webhook 已删除"}

    @router.post("/api/webhooks/{webhook_id}/test", dependencies=[Depends(verify_token)])
    def test_webhook(webhook_id: int):
        wh = webhook_service.get(webhook_id)
        if not wh:
            raise HTTPException(status_code=404, detail=f"未找到 webhook: {webhook_id}")
        if not webhook_service.test(wh):
            raise HTTPException(status_code=503, detail="Webhook 队列已满，请稍后重试")
        return {"success": True, "message": "测试请求已发送"}

    return router
