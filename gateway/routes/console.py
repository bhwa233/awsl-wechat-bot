"""微信控制台调试路由."""

import os
import sys
import threading
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field

from common.auth import verify_token
from common.models import MessagePayload

router = APIRouter()
TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "templates")


class ConsoleWindowDebugRequest(BaseModel):
    handle: int
    depth: int = Field(default=5, ge=0, le=12)
    path: str = ""
    max_nodes: int = Field(default=2000, ge=1, le=10000)
    include_patterns: bool = False


class ConsoleStateRequest(BaseModel):
    handle: int | None = None
    max_nodes: int = Field(default=5000, ge=1, le=10000)


class ConsoleOpenChatRequest(BaseModel):
    path: str = Field(min_length=1, max_length=512)
    max_nodes: int = Field(default=5000, ge=1, le=10000)


class ConsoleScrollChatsRequest(BaseModel):
    direction: Literal["up", "down"]
    max_nodes: int = Field(default=5000, ge=1, le=10000)


class ConsoleSendMessageRequest(MessagePayload):
    chat_name: str = Field(min_length=1, max_length=256)
    max_nodes: int = Field(default=5000, ge=1, le=10000)


def create_routes(gateway_app=None):
    console_operation_lock = threading.Lock()

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

        return _windows_service(gateway_app, console_operation_lock).open_chat(
            path=request.path,
            max_nodes=request.max_nodes,
        )

    @router.post("/api/console/scroll-chats", dependencies=[Depends(verify_token)])
    def console_scroll_chats(request: ConsoleScrollChatsRequest):
        """向上或向下滚动 Windows 微信主窗口聊天列表."""
        if sys.platform != "win32":
            raise HTTPException(status_code=501, detail="console/scroll-chats 当前只支持 Windows")

        return _windows_service(gateway_app, console_operation_lock).scroll_chats(
            direction=request.direction,
            max_nodes=request.max_nodes,
        )

    @router.post("/api/console/send-message", dependencies=[Depends(verify_token)])
    def console_send_message(request: ConsoleSendMessageRequest):
        """在主窗口当前聊天输入框发送文本、图片、文件或语音."""
        if sys.platform != "win32":
            raise HTTPException(status_code=501, detail="console/send-message 当前只支持 Windows")

        return _windows_service(gateway_app, console_operation_lock).send_message(
            message_type=request.message_type,
            message=request.message or "",
            filename=request.filename or "",
            chat_name=request.chat_name,
            max_nodes=request.max_nodes,
        )

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
            include_patterns=request.include_patterns,
        )

    return router


def _serve_console_file(filename: str, media_type: str):
    path = os.path.join(TEMPLATES_DIR, filename)
    try:
        with open(path, encoding="utf-8") as f:
            return Response(content=f.read(), media_type=media_type)
    except FileNotFoundError:
        return Response(status_code=404)


def _windows_service(gateway_app=None, console_operation_lock=None):
    if gateway_app is None:
        from gateway.console.windows import WindowsConsoleDebugService

        return WindowsConsoleDebugService(console_operation_lock=console_operation_lock)

    from gateway.adapters.windows import WindowsWeChatAdapter
    from gateway.console.windows import WindowsConsoleDebugService
    from gateway.utils.voice_synthesis import synthesize_voice

    adapter: WindowsWeChatAdapter = gateway_app.adapter
    return WindowsConsoleDebugService(
        ui_operation_lock=adapter.ui_operation_lock,
        console_operation_lock=console_operation_lock,
        activate_window=adapter.activate_specific_window,
        set_clipboard_text=adapter.set_clipboard_text_verified,
        send_image_to_window=adapter.send_image_to_window,
        send_file_to_window=adapter.send_file_to_window,
        send_voice_to_window=adapter.send_voice_to_window,
        synthesize_voice=synthesize_voice,
    )
