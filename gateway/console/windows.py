"""Windows 微信主窗口 UIA 调试信息."""

from __future__ import annotations

import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)

_WECHAT_WINDOW_CLASSES = {"mmui::MainWindow", "mmui::ChatSingleWindow", "WeChatMainWndForPC"}
_MAIN_WINDOW_CLASSES = {"mmui::MainWindow", "WeChatMainWndForPC"}
_MAIN_WINDOW_TITLES = {"微信", "WeChat", "Weixin"}


class WindowsConsoleDebugService:
    """读取 Windows 微信主窗口 UIA 树."""

    def __init__(self):
        self._thread_inits: dict[int, Any] = {}

    def list_windows(self) -> dict[str, Any]:
        auto = self._ensure_uia()
        windows = []
        for control in self._iter_desktop_windows(auto):
            info = self._control_info(control)
            if not self._is_wechat_window(info):
                continue
            info["is_main"] = self._is_main_window(info)
            windows.append(info)

        return {
            "available": bool(windows),
            "platform": "windows",
            "message": "" if windows else "未找到微信窗口",
            "windows": windows,
        }

    def dump_window(self, *, handle: int, max_depth: int = 5, path: str = "", max_nodes: int = 2000) -> dict[str, Any]:
        auto = self._ensure_uia()
        window = self._find_window_by_handle(auto, handle)
        if window is None:
            return {
                "available": False,
                "platform": "windows",
                "message": f"未找到窗口 handle={handle}",
                "window": None,
                "tree": None,
            }

        info = self._control_info(window)
        if not self._is_wechat_window(info):
            return {
                "available": False,
                "platform": "windows",
                "message": f"handle={handle} 不是微信窗口",
                "window": info,
                "tree": None,
            }

        try:
            path_indexes = self._parse_path(path)
        except ValueError:
            return {
                "available": False,
                "platform": "windows",
                "message": f"无效 path={path}",
                "window": info,
                "tree": None,
            }

        target = self._find_by_path(window, path_indexes)
        if target is None:
            return {
                "available": False,
                "platform": "windows",
                "message": f"未找到子节点 path={path}",
                "window": info,
                "tree": None,
            }

        counter = {"count": 0}
        return {
            "available": True,
            "platform": "windows",
            "message": "",
            "window": info,
            "tree": self._dump_control(
                target,
                max_depth=max_depth,
                depth=0,
                path=path_indexes,
                counter=counter,
                max_nodes=max_nodes,
            ),
            "node_count": counter["count"],
        }

    def _ensure_uia(self):
        import uiautomation as auto

        tid = threading.get_ident()
        if tid not in self._thread_inits:
            self._thread_inits[tid] = auto.UIAutomationInitializerInThread()
            logger.debug("console 线程 %s UIA 初始化完成", tid)
        return auto

    def _find_main_window(self, auto):
        for control in self._iter_desktop_windows(auto):
            info = self._control_info(control)
            if self._is_main_window(info):
                return control
        return None

    def _find_window_by_handle(self, auto, handle: int):
        for control in self._iter_desktop_windows(auto):
            info = self._control_info(control)
            if info["native_window_handle"] == handle:
                return control
        return None

    @staticmethod
    def _iter_desktop_windows(auto):
        root = auto.GetRootControl()
        return root.GetChildren()

    @staticmethod
    def _is_wechat_window(info: dict[str, Any]) -> bool:
        if info["class_name"] in _WECHAT_WINDOW_CLASSES:
            return True
        return info["name"] in _MAIN_WINDOW_TITLES and info["control_type"] == "WindowControl"

    @staticmethod
    def _is_main_window(info: dict[str, Any]) -> bool:
        if info["class_name"] in _MAIN_WINDOW_CLASSES:
            return True
        return info["name"] in _MAIN_WINDOW_TITLES and info["control_type"] == "WindowControl"

    def _find_by_path(self, control, indexes: tuple[int, ...]):
        current = control
        for index in indexes:
            children = self._safe_call(current, "GetChildren", default=[])
            if index < 0 or index >= len(children):
                return None
            current = children[index]
        return current

    @staticmethod
    def _parse_path(path: str) -> tuple[int, ...]:
        path = path.strip()
        if not path:
            return ()
        return tuple(int(part) for part in path.replace("/", ".").split(".") if part != "")

    def _dump_control(
        self,
        control,
        *,
        max_depth: int,
        depth: int,
        path: tuple[int, ...],
        counter: dict[str, int],
        max_nodes: int,
    ) -> dict[str, Any]:
        counter["count"] += 1
        node = self._control_info(control)
        node["depth"] = depth
        node["path"] = ".".join(str(part) for part in path)
        node["children"] = []
        if depth >= max_depth:
            node["truncated"] = "depth"
            node["child_count"] = None
            return node

        children = self._safe_call(control, "GetChildren", default=[])
        node["child_count"] = len(children)
        for index, child in enumerate(children):
            if counter["count"] >= max_nodes:
                node["truncated"] = "max_nodes"
                break
            node["children"].append(
                self._dump_control(
                    child,
                    max_depth=max_depth,
                    depth=depth + 1,
                    path=(*path, index),
                    counter=counter,
                    max_nodes=max_nodes,
                )
            )
        return node

    def _control_info(self, control) -> dict[str, Any]:
        return {
            "name": self._safe_attr(control, "Name", ""),
            "class_name": self._safe_attr(control, "ClassName", ""),
            "control_type": self._safe_attr(control, "ControlTypeName", ""),
            "automation_id": self._safe_attr(control, "AutomationId", ""),
            "framework_id": self._safe_attr(control, "FrameworkId", ""),
            "process_id": self._safe_attr(control, "ProcessId", None),
            "native_window_handle": self._safe_attr(control, "NativeWindowHandle", None),
            "enabled": self._safe_attr(control, "IsEnabled", None),
            "offscreen": self._safe_attr(control, "IsOffscreen", None),
            "rect": self._rect_to_dict(self._safe_attr(control, "BoundingRectangle", None)),
            "runtime_id": self._runtime_id(control),
        }

    @staticmethod
    def _safe_attr(obj, name: str, default: Any = None) -> Any:
        try:
            return getattr(obj, name)
        except Exception:
            return default

    @staticmethod
    def _safe_call(obj, name: str, default: Any = None) -> Any:
        try:
            return getattr(obj, name)()
        except Exception:
            return default

    @staticmethod
    def _rect_to_dict(rect) -> dict[str, int] | None:
        if rect is None:
            return None
        try:
            left = int(rect.left)
            top = int(rect.top)
            right = int(rect.right)
            bottom = int(rect.bottom)
            return {
                "left": left,
                "top": top,
                "right": right,
                "bottom": bottom,
                "width": right - left,
                "height": bottom - top,
            }
        except Exception:
            return None

    @staticmethod
    def _runtime_id(control) -> list[int]:
        try:
            runtime_id = control.GetRuntimeId()
        except Exception:
            return []
        try:
            return [int(part) for part in runtime_id]
        except Exception:
            return []
