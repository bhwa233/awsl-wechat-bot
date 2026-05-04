"""Chatlog HTTP API 服务器"""
from fastapi import FastAPI

from chatlog.routes import create_routes


def create_app() -> FastAPI:
    app = FastAPI(
        title="AWSL Chatlog API",
        version="1.0.0",
    )
    app.include_router(create_routes())
    return app
