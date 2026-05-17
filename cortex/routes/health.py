"""Cortex 健康检查和首页"""
import os
from datetime import datetime

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

from common.config import config

router = APIRouter()

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'templates')


def _serve_file(filename: str, media_type: str):
    path = os.path.join(TEMPLATES_DIR, filename)
    try:
        with open(path, encoding='utf-8') as f:
            return Response(content=f.read(), media_type=media_type)
    except FileNotFoundError:
        return Response(status_code=404)


def create_routes():
    @router.get("/", response_class=HTMLResponse)
    async def root():
        try:
            with open(os.path.join(TEMPLATES_DIR, 'index.html'), encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            return "<h1>模板文件未找到</h1>"

    @router.get("/console", response_class=HTMLResponse)
    async def console():
        try:
            with open(os.path.join(TEMPLATES_DIR, 'console.html'), encoding='utf-8') as f:
                return f.read()
        except FileNotFoundError:
            return "<h1>Console 模板文件未找到</h1>"

    @router.get("/style.css")
    async def style_css():
        return _serve_file('style.css', 'text/css')

    @router.get("/app.js")
    async def app_js():
        return _serve_file('app.js', 'application/javascript')

    @router.get("/console.js")
    async def console_js():
        return _serve_file('console.js', 'application/javascript')

    @router.get("/manifest.json")
    async def manifest():
        return {
            "name": "AWSL WeChat Bot",
            "short_name": "AWSL Bot",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#f4f4f2",
            "theme_color": "#22c55e",
            "icons": [
                {
                    "src": (
                        "data:image/svg+xml,"
                        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'>"
                        "<text y='.9em' font-size='90'>🤖</text></svg>"
                    ),
                    "sizes": "any",
                    "type": "image/svg+xml",
                }
            ],
        }

    @router.get("/chat.js")
    async def chat_js():
        return _serve_file('chat.js', 'application/javascript')

    @router.get("/tasks.js")
    async def tasks_js():
        return _serve_file('tasks.js', 'application/javascript')

    @router.get("/webhooks.js")
    async def webhooks_js():
        return _serve_file('webhooks.js', 'application/javascript')

    @router.get("/api/health")
    async def health():
        now = datetime.now()
        utc_offset = now.astimezone().utcoffset()
        offset_hours = int(utc_offset.total_seconds() // 3600) if utc_offset else 0
        return {
            "service": "cortex",
            "status": "running",
            "auth_enabled": bool(config.HTTP_API_TOKEN),
            "server_time": now.isoformat(),
            "timezone": str(now.astimezone().tzinfo),
            "timezone_offset": f"{offset_hours:+d}",
        }

    return router
