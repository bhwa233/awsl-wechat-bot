"""微信控制台调试路由."""

import sys

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from common.auth import verify_token

router = APIRouter()


class ConsoleWindowDebugRequest(BaseModel):
    handle: int
    depth: int = Field(default=5, ge=0, le=12)
    path: str = ""
    max_nodes: int = Field(default=2000, ge=1, le=10000)


class ConsoleStateRequest(BaseModel):
    handle: int | None = None
    max_nodes: int = Field(default=5000, ge=1, le=10000)


def create_routes():
    @router.post("/api/console/state", dependencies=[Depends(verify_token)])
    def console_state(request: ConsoleStateRequest):
        """返回 Windows 微信窗口的聊天列表、聊天区和输入框状态."""
        if sys.platform != "win32":
            raise HTTPException(status_code=501, detail="console/state 当前只支持 Windows")

        from gateway.console.windows import WindowsConsoleDebugService

        return WindowsConsoleDebugService().get_state(handle=request.handle, max_nodes=request.max_nodes)

    @router.post("/api/console/debug/list", dependencies=[Depends(verify_token)])
    def console_debug_list():
        """列出 Windows 微信窗口."""
        if sys.platform != "win32":
            raise HTTPException(status_code=501, detail="console/debug 当前只支持 Windows")

        from gateway.console.windows import WindowsConsoleDebugService

        return WindowsConsoleDebugService().list_windows()

    @router.post("/api/console/debug/inspect", dependencies=[Depends(verify_token)])
    def console_debug_inspect(request: ConsoleWindowDebugRequest):
        """返回指定 Windows 微信窗口或子节点 UIA 调试树."""
        if sys.platform != "win32":
            raise HTTPException(status_code=501, detail="console/debug 当前只支持 Windows")

        from gateway.console.windows import WindowsConsoleDebugService

        return WindowsConsoleDebugService().dump_window(
            handle=request.handle,
            max_depth=request.depth,
            path=request.path,
            max_nodes=request.max_nodes,
        )

    return router
