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
    pycrypto_router = create_routes()
    dll_router = create_v2_router()

    app.include_router(pycrypto_router, prefix="/api/chatlog/v1", tags=["chatlog-v1"])
    app.include_router(dll_router, prefix="/api/chatlog/v2", tags=["chatlog-v2"])
    app.include_router(dll_router if config.CHATLOG_USE_V2 else pycrypto_router, prefix="/api/chatlog", tags=["chatlog"])
    return app
