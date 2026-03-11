"""Cortex 健康检查和首页"""
import os
from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()


def create_routes():
    @router.get("/", response_class=HTMLResponse)
    async def root():
        template_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates', 'index.html')
        try:
            with open(template_path, 'r', encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            return "<h1>模板文件未找到</h1>"

    @router.get("/api/health")
    async def health():
        return {"service": "cortex", "status": "running"}

    return router
