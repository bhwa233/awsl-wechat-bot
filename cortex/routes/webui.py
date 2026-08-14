"""Cortex Web UI 路由"""
import os

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter()

TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")


def create_routes():
    @router.get("/", response_class=HTMLResponse)
    async def root():
        try:
            with open(os.path.join(TEMPLATES_DIR, "index.html"), encoding="utf-8") as f:
                return f.read()
        except FileNotFoundError:
            return "<h1>模板文件未找到</h1>"

    @router.get("/manifest.json")
    async def manifest():
        return {
            "name": "AWSL WeChat Bot",
            "short_name": "AWSL Bot",
            "start_url": "/",
            "display": "standalone",
            "background_color": "#ededed",
            "theme_color": "#35d18c",
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

    return router
