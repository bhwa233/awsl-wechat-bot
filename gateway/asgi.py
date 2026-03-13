"""Gateway HTTP API 服务器"""
from contextlib import asynccontextmanager

from fastapi import FastAPI

from gateway.routes import health, groups, messages, ws, chatlog


def create_app(gateway_app) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        gateway_app.start()
        yield
        gateway_app.stop()

    app = FastAPI(
        title="AWSL Gateway API",
        version="1.0.0",
        lifespan=lifespan,
    )
    app.include_router(health.create_routes(gateway_app))
    app.include_router(groups.create_routes(gateway_app))
    app.include_router(messages.create_routes(gateway_app.gateway))
    app.include_router(ws.create_routes(gateway_app.gateway))
    app.include_router(chatlog.create_routes(gateway_app.gateway))
    return app
