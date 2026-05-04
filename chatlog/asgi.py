"""Chatlog HTTP API 服务器"""
from fastapi import FastAPI

from chatlog.routes import create_routes
from chatlog.routes_v2 import create_v2_routes


def create_app() -> FastAPI:
    app = FastAPI(
        title="AWSL Chatlog API",
        version="1.0.0",
    )
    app.include_router(create_routes())
    for router in create_v2_routes():
        app.include_router(router)
    return app
