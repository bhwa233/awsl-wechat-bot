"""微信控制台调试路由."""

import os
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, Field, field_validator, model_validator

from common.auth import verify_token
from common.models import MAX_TEXT_MESSAGE_LENGTH, MessagePayload, MessageType
from gateway.console.adapters.base import BaseConsoleAdapter

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


class ConsoleStandaloneOpenRequest(ConsoleOpenChatRequest):
    chat_id: str | None = Field(default=None, min_length=1, max_length=256)


class ConsoleScrollChatsRequest(BaseModel):
    direction: Literal["up", "down"]
    max_nodes: int = Field(default=5000, ge=1, le=10000)


class ConsoleStandaloneCloseRequest(BaseModel):
    window_id: int | None = Field(default=None, gt=0)
    chat_id: str | None = Field(default=None, min_length=1, max_length=256)

    @model_validator(mode="after")
    def validate_target(self):
        if self.window_id is None and self.chat_id is None:
            raise ValueError("window_id 和 chat_id 至少提供一个")
        return self


class ConsoleSendMessageRequest(MessagePayload):
    chat_name: str = Field(min_length=1, max_length=256)
    max_nodes: int = Field(default=5000, ge=1, le=10000)
    reply_to_message: str | None = Field(default=None, max_length=MAX_TEXT_MESSAGE_LENGTH)

    @field_validator("reply_to_message")
    @classmethod
    def validate_reply_to_message(cls, value: str | None) -> str | None:
        return value if value and value.strip() else None

    @model_validator(mode="after")
    def check_reply(self):
        if self.reply_to_message is not None and self.message_type != MessageType.TEXT:
            raise ValueError("reply_to_message 当前仅支持文本回复")
        return self


def create_routes(gateway_app):
    @router.get("/console", response_class=HTMLResponse)
    def console_page():
        return _serve_console_file("console.html", "text/html")

    @router.get("/console.js")
    def console_js():
        return _serve_console_file("console.js", "application/javascript")

    @router.get("/api/console/wechat-startup", dependencies=[Depends(verify_token)])
    def wechat_startup_status():
        return gateway_app.wechat_startup.status

    @router.post("/api/console/wechat-startup", dependencies=[Depends(verify_token)])
    def start_wechat():
        startup = gateway_app.wechat_startup
        if not startup.supported:
            raise HTTPException(status_code=501, detail="当前平台不支持自动登录")
        accepted = startup.start(manual=True)
        return {"accepted": accepted, **startup.status}

    @router.post("/api/console/state", dependencies=[Depends(verify_token)])
    def console_state(request: ConsoleStateRequest):
        """返回微信主窗口的聊天列表、聊天区和输入框状态."""
        return _console_adapter(gateway_app).get_state(handle=request.handle, max_nodes=request.max_nodes)

    @router.post("/api/console/open-chat", dependencies=[Depends(verify_token)])
    def console_open_chat(request: ConsoleOpenChatRequest):
        """按 state 返回的 path 点击主窗口聊天列表项,并聚焦输入框."""
        return _console_adapter(gateway_app).open_chat(
            path=request.path,
            max_nodes=request.max_nodes,
        )

    @router.post("/api/console/scroll-chats", dependencies=[Depends(verify_token)])
    def console_scroll_chats(request: ConsoleScrollChatsRequest):
        """向上或向下滚动 微信主窗口聊天列表."""
        return _console_adapter(gateway_app).scroll_chats(
            direction=request.direction,
            max_nodes=request.max_nodes,
        )

    @router.post("/api/console/send-message", dependencies=[Depends(verify_token)])
    def console_send_message(request: ConsoleSendMessageRequest):
        """在主窗口当前聊天输入框发送文本、图片、文件或语音."""
        return _console_adapter(gateway_app).send_message(
            message_type=request.message_type,
            message=request.message or "",
            filename=request.filename or "",
            chat_name=request.chat_name,
            max_nodes=request.max_nodes,
            reply_to_message=request.reply_to_message,
            mention_all=request.mention_all,
        )

    @router.get("/api/console/standalone-windows", dependencies=[Depends(verify_token)])
    def console_standalone_windows():
        """列出全部 微信独立聊天窗口."""
        return _console_adapter(gateway_app).list_standalone_windows()

    @router.post("/api/console/standalone-windows/open", dependencies=[Depends(verify_token)])
    def console_open_standalone_window(request: ConsoleStandaloneOpenRequest):
        """双击主窗口 ChatSessionCell 打开独立聊天窗口."""
        return _console_adapter(gateway_app).open_standalone_window(
            path=request.path,
            chat_id=request.chat_id,
            max_nodes=request.max_nodes,
        )

    @router.get("/api/console/standalone-windows/status", dependencies=[Depends(verify_token)])
    def console_standalone_window_status(chat_id: str | None = None):
        """查询会话是否已有独立窗口，不暴露窗口调试字段."""
        return _console_adapter(gateway_app).standalone_window_status(chat_id=chat_id)

    @router.post("/api/console/standalone-windows/close", dependencies=[Depends(verify_token)])
    def console_close_standalone_window(request: ConsoleStandaloneCloseRequest):
        """按 HWND 或会话 ID 精确关闭独立聊天窗口."""
        return _console_adapter(gateway_app).close_standalone_window(
            window_id=request.window_id,
            chat_id=request.chat_id,
        )

    @router.post("/api/console/standalone-windows/cache", dependencies=[Depends(verify_token)])
    def console_cache_standalone_windows():
        """将当前独立聊天窗口快照写入用户 Home."""
        return _console_adapter(gateway_app).cache_standalone_windows()

    @router.get("/api/console/standalone-windows/cache", dependencies=[Depends(verify_token)])
    def console_get_cached_standalone_windows():
        """读取用户 Home 中的独立聊天窗口快照."""
        return _console_adapter(gateway_app).get_cached_standalone_windows()

    @router.post("/api/console/standalone-windows/open-cached", dependencies=[Depends(verify_token)])
    def console_open_cached_standalone_windows():
        """按 Home 缓存批量恢复独立聊天窗口."""
        return _console_adapter(gateway_app).open_cached_standalone_windows()

    @router.post("/api/console/debug/list", dependencies=[Depends(verify_token)])
    def console_debug_list():
        """列出 微信窗口."""
        return _console_adapter(gateway_app).list_windows()

    @router.post("/api/console/debug/inspect", dependencies=[Depends(verify_token)])
    def console_debug_inspect(request: ConsoleWindowDebugRequest):
        """返回指定 微信窗口或子节点 UIA/AT-SPI 调试树."""
        return _console_adapter(gateway_app).dump_window(
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


def _console_adapter(gateway_app) -> BaseConsoleAdapter:
    adapter = gateway_app.console_adapter
    if adapter is None:
        raise HTTPException(status_code=501, detail="Console 当前平台不可用")
    return adapter
