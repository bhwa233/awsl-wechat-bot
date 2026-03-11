"""Cortex HTTP API 服务器"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common.config import config
from cortex.services.scheduled_task import ScheduledTaskService
from cortex.services.webhook import WebhookService
from cortex.scheduler import TaskScheduler
from cortex.routes import health, tasks, webhooks


def create_app(cortex_app) -> FastAPI:
    project_root = os.path.dirname(os.path.dirname(__file__))

    task_service = ScheduledTaskService(os.path.join(project_root, 'scheduled_tasks.db'))
    webhook_service = WebhookService(os.path.join(project_root, 'webhooks.db'))
    scheduler = TaskScheduler(
        task_service,
        cortex_app.gateway_client,
        gateway_base_url=config.HTTP_API_BASE_URL,
        gateway_token=config.HTTP_API_TOKEN,
    )

    cortex_app.gateway_client.add_listener(cortex_app)
    cortex_app.gateway_client.add_listener(webhook_service)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        cortex_app.start()
        scheduler.start()
        yield
        scheduler.stop()
        cortex_app.stop()
        webhook_service.close()
        task_service.close()

    app = FastAPI(
        title="AWSL Cortex API",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.include_router(health.create_routes())
    app.include_router(tasks.create_routes(task_service))
    app.include_router(webhooks.create_routes(webhook_service))
    return app
