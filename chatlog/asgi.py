"""Chatlog HTTP API 服务器"""
from fastapi import FastAPI

from chatlog.routes import create_routes
from chatlog.routes_v2 import create_v2_router
from common.config import config


def create_app() -> FastAPI:
    app = FastAPI(
        title="AWSL Chatlog API",
        version="1.0.0",
    )
    v1_pycrypto_router = create_routes()
    v2_dll_router = create_v2_router()

    app.include_router(v1_pycrypto_router, prefix="/api/chatlog/v1", tags=["chatlog-v1"])
    app.include_router(v2_dll_router, prefix="/api/chatlog/v2", tags=["chatlog-v2"])
    app.include_router(v2_dll_router if config.CHATLOG_USE_V2 else v1_pycrypto_router, prefix="/api/chatlog", tags=["chatlog"])
    return app
