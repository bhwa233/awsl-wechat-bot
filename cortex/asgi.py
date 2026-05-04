"""Cortex HTTP API 服务器"""
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common.config import config
from cortex.scheduler import TaskScheduler
from cortex.routes import health, tasks, webhooks, gateway_proxy, chatlog_proxy, summary
from cortex.services.chatlog_client import ChatlogClient
from cortex.services.scheduled_task import ScheduledTaskService
from cortex.services.summary_service import SummaryService
from cortex.services.webhook import WebhookService

logger = logging.getLogger(__name__)


def create_app(cortex_app) -> FastAPI:
    project_root = os.path.dirname(os.path.dirname(__file__))
    task_service = ScheduledTaskService(os.path.join(project_root, "scheduled_tasks.db"))
    webhook_service = WebhookService(
        os.path.join(project_root, "webhooks.db"),
        gateway_client=cortex_app.gateway_client,
    )

    chatlog_host = config.CHATLOG_HTTP_HOST if config.CHATLOG_HTTP_HOST != "0.0.0.0" else "127.0.0.1"
    chatlog_base_url = f"http://{chatlog_host}:{config.CHATLOG_HTTP_PORT}"
    chatlog_client = ChatlogClient(base_url=chatlog_base_url, token=config.HTTP_API_TOKEN)
    summary_service = SummaryService(chatlog_client, cortex_app.gateway_client)

    scheduler = TaskScheduler(
        task_service,
        cortex_app.gateway_client,
        summary_service=summary_service,
        gateway_base_url=config.HTTP_API_BASE_URL,
        gateway_token=config.HTTP_API_TOKEN,
    )

    gw_proxy_router, gw_proxy_client = gateway_proxy.create_routes(config.HTTP_API_BASE_URL, config.HTTP_API_TOKEN)
    cl_proxy_router, cl_proxy_client = chatlog_proxy.create_routes(chatlog_base_url, config.HTTP_API_TOKEN)

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
            for name, cleanup in [
                ("调度器", scheduler.stop),
                ("Cortex 主服务", cortex_app.stop),
                ("Summary 服务", summary_service.close),
                ("Gateway 代理客户端", gw_proxy_client.aclose),
                ("Chatlog 代理客户端", cl_proxy_client.aclose),
                ("Chatlog 客户端", chatlog_client.close),
                ("webhook 服务", webhook_service.close),
                ("任务服务", task_service.close),
            ]:
                try:
                    result = cleanup()
                    if hasattr(result, "__await__"):
                        await result
                except Exception as e:
                    logger.warning(f"关闭{name}失败: {e}")

    app = FastAPI(
        title="AWSL Cortex API",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.include_router(health.create_routes())
    app.include_router(tasks.create_routes(task_service))
    app.include_router(webhooks.create_routes(webhook_service))
    app.include_router(summary.create_routes(summary_service))
    app.include_router(gw_proxy_router)
    app.include_router(cl_proxy_router)
    return app
