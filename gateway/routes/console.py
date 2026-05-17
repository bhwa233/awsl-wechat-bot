"""微信控制台调试路由."""

import os
import sys

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from common.auth import verify_token

router = APIRouter()
TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "cortex", "templates")


class ConsoleWindowDebugRequest(BaseModel):
    handle: int
    depth: int = Field(default=5, ge=0, le=12)
    path: str = ""
    max_nodes: int = Field(default=2000, ge=1, le=10000)


class ConsoleStateRequest(BaseModel):
    handle: int | None = None
    max_nodes: int = Field(default=5000, ge=1, le=10000)


class ConsoleOpenChatRequest(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    max_nodes: int = Field(default=5000, ge=1, le=10000)


class ConsoleSendTextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)
    max_nodes: int = Field(default=5000, ge=1, le=10000)


def create_routes(gateway_app=None):
    @router.get("/console", response_class=HTMLResponse)
    def console_page():
        return _serve_console_file("console.html", "text/html")

    @router.get("/console.js")
    def console_js():
        return _serve_console_file("console.js", "application/javascript")

    @router.post("/api/console/state", dependencies=[Depends(verify_token)])
    def console_state(request: ConsoleStateRequest):
        """返回 Windows 微信窗口的聊天列表、聊天区和输入框状态."""
        if sys.platform != "win32":
            raise HTTPException(status_code=501, detail="console/state 当前只支持 Windows")

        return _windows_service(gateway_app).get_state(handle=request.handle, max_nodes=request.max_nodes)

    @router.post("/api/console/open-chat", dependencies=[Depends(verify_token)])
    def console_open_chat(request: ConsoleOpenChatRequest):
        """按 state 返回的 path 点击主窗口聊天列表项,并聚焦输入框."""
        if sys.platform != "win32":
            raise HTTPException(status_code=501, detail="console/open-chat 当前只支持 Windows")

        return _windows_service(gateway_app).open_chat(path=request.path, max_nodes=request.max_nodes)

    @router.post("/api/console/send-text", dependencies=[Depends(verify_token)])
    def console_send_text(request: ConsoleSendTextRequest):
        """在主窗口当前聊天输入框发送文本."""
        if sys.platform != "win32":
            raise HTTPException(status_code=501, detail="console/send-text 当前只支持 Windows")

        return _windows_service(gateway_app).send_text(text=request.text, max_nodes=request.max_nodes)

    @router.post("/api/console/debug/list", dependencies=[Depends(verify_token)])
    def console_debug_list():
        """列出 Windows 微信窗口."""
        if sys.platform != "win32":
            raise HTTPException(status_code=501, detail="console/debug 当前只支持 Windows")

        return _windows_service(gateway_app).list_windows()

    @router.post("/api/console/debug/inspect", dependencies=[Depends(verify_token)])
    def console_debug_inspect(request: ConsoleWindowDebugRequest):
        """返回指定 Windows 微信窗口或子节点 UIA 调试树."""
        if sys.platform != "win32":
            raise HTTPException(status_code=501, detail="console/debug 当前只支持 Windows")

        return _windows_service(gateway_app).dump_window(
            handle=request.handle,
            max_depth=request.depth,
            path=request.path,
            max_nodes=request.max_nodes,
        )

    return router


def _serve_console_file(filename: str, media_type: str):
    path = os.path.join(TEMPLATES_DIR, filename)
    try:
        with open(path, encoding="utf-8") as f:
            return Response(content=f.read(), media_type=media_type)
    except FileNotFoundError:
        return Response(status_code=404)


def _windows_service(gateway_app=None):
    if gateway_app is None:
        from gateway.console.windows import WindowsConsoleDebugService

        return WindowsConsoleDebugService()

    from gateway.adapters.windows import WindowsWeChatAdapter
    from gateway.console.windows import WindowsConsoleDebugService

    adapter: WindowsWeChatAdapter = gateway_app.adapter
    return WindowsConsoleDebugService(
        ui_operation_lock=adapter.ui_operation_lock,
        activate_window=adapter.activate_specific_window,
        set_clipboard_text=adapter.set_clipboard_text_verified,
    )
