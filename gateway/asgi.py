"""Gateway HTTP API 服务器"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from common.logging import setup_uvicorn_logging
from gateway.metrics import metrics
from gateway.routes import console, groups, health, messages, ws


def create_app(gateway_app) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        setup_uvicorn_logging()
        gateway_app.start()
        yield
        gateway_app.stop()

    app = FastAPI(
        title="AWSL Gateway API",
        version="1.0.0",
        lifespan=lifespan,
        middleware=[metrics.middleware],
    )
    app.include_router(metrics.router)
    app.include_router(health.create_routes(gateway_app))
    app.include_router(console.create_routes(gateway_app))
    app.include_router(groups.create_routes(gateway_app))
    app.include_router(messages.create_routes(gateway_app.gateway))
    app.include_router(ws.create_routes(gateway_app.gateway))
    return app
