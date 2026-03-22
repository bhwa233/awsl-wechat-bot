"""Cortex HTTP API 服务器"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common.config import config
from cortex.scheduler import TaskScheduler
from cortex.routes import health, tasks, webhooks, gateway_proxy
from cortex.services.scheduled_task import ScheduledTaskService
from cortex.services.webhook import WebhookService

logger = logging.getLogger(__name__)

def create_app(cortex_app) -> FastAPI:
    project_root = os.path.dirname(os.path.dirname(__file__))
    task_service = ScheduledTaskService(os.path.join(project_root, "scheduled_tasks.db"))
    webhook_service = WebhookService(
        os.path.join(project_root, "webhooks.db"),
        gateway_client=cortex_app.gateway_client,
    )
    scheduler = TaskScheduler(
        task_service,
        cortex_app.gateway_client,
        gateway_base_url=config.HTTP_API_BASE_URL,
        gateway_token=config.HTTP_API_TOKEN,
    )
    proxy_router, proxy_client = gateway_proxy.create_routes(config.HTTP_API_BASE_URL, config.HTTP_API_TOKEN)
    cortex_app.gateway_client.add_listener(cortex_app)
    cortex_app.gateway_client.add_listener(webhook_service)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        try:
            cortex_app.start()
            scheduler.start()
            yield
        except Exception:
            logger.exception("Cortex 启动或运行时出现异常")
            raise
        finally:
            try:
                scheduler.stop()
            except Exception as e:
                logger.warning(f"停止调度器失败: {e}")
            try:
                cortex_app.stop()
            except Exception as e:
                logger.warning(f"停止 Cortex 主服务失败: {e}")
            try:
                await proxy_client.aclose()
            except Exception as e:
                logger.warning(f"关闭代理客户端失败: {e}")
            try:
                webhook_service.close()
            except Exception as e:
                logger.warning(f"关闭 webhook 服务失败: {e}")
            try:
                task_service.close()
            except Exception as e:
                logger.warning(f"关闭任务服务失败: {e}")

    app = FastAPI(
        title="AWSL Cortex API",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.include_router(health.create_routes())
    app.include_router(tasks.create_routes(task_service))
    app.include_router(webhooks.create_routes(webhook_service))
    app.include_router(proxy_router)
    return app
