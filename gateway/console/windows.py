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

    def get_state(self, *, handle: int | None = None, max_nodes: int = 5000) -> dict[str, Any]:
        auto = self._ensure_uia()
        window = self._resolve_window(auto, handle)
        if window is None:
            message = f"未找到窗口 handle={handle}" if handle else "未找到微信主窗口"
            return {
                "available": False,
                "platform": "windows",
                "message": message,
                "window": None,
                "chat_list": {"available": False, "items": []},
                "chat_area": {"available": False, "messages": []},
                "input_box": {"available": False},
            }

        info = self._control_info(window)
        if not self._is_wechat_window(info):
            return {
                "available": False,
                "platform": "windows",
                "message": f"handle={handle} 不是微信窗口",
                "window": info,
                "chat_list": {"available": False, "items": []},
                "chat_area": {"available": False, "messages": []},
                "input_box": {"available": False},
            }

        nodes = self._collect_nodes(window, max_depth=12, max_nodes=max_nodes)
        return {
            "available": True,
            "platform": "windows",
            "message": "",
            "window": info,
            "chat_list": self._parse_chat_list(nodes),
            "chat_area": self._parse_chat_area(nodes, info),
            "input_box": self._parse_input_box(nodes),
            "node_count": len(nodes),
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

    def _resolve_window(self, auto, handle: int | None):
        if handle is None:
            return self._find_main_window(auto)
        return self._find_window_by_handle(auto, handle)

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

    def _collect_nodes(self, control, *, max_depth: int, max_nodes: int) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        self._collect_node(control, path=(), depth=0, max_depth=max_depth, max_nodes=max_nodes, nodes=nodes)
        return nodes

    def _collect_node(
        self,
        control,
        *,
        path: tuple[int, ...],
        depth: int,
        max_depth: int,
        max_nodes: int,
        nodes: list[dict[str, Any]],
    ) -> None:
        if len(nodes) >= max_nodes:
            return

        info = self._control_info(control)
        info["path"] = ".".join(str(part) for part in path)
        info["depth"] = depth
        info["_control"] = control
        nodes.append(info)

        if depth >= max_depth:
            return

        children = self._safe_call(control, "GetChildren", default=[])
        for index, child in enumerate(children):
            if len(nodes) >= max_nodes:
                return
            self._collect_node(
                child,
                path=(*path, index),
                depth=depth + 1,
                max_depth=max_depth,
                max_nodes=max_nodes,
                nodes=nodes,
            )

    def _parse_chat_list(self, nodes: list[dict[str, Any]]) -> dict[str, Any]:
        list_node = self._first_node(
            nodes,
            lambda node: node["control_type"] == "ListControl"
            and (node["automation_id"] == "session_list" or (node["class_name"] == "mmui::XTableView" and node["name"] == "会话")),
        )
        items = [
            self._chat_list_item(node)
            for node in nodes
            if node["class_name"] == "mmui::ChatSessionCell" or node["automation_id"].startswith("session_item_")
        ]
        return {
            "available": list_node is not None,
            "path": "" if list_node is None else list_node["path"],
            "rect": None if list_node is None else list_node["rect"],
            "items": items,
        }

    def _parse_chat_area(self, nodes: list[dict[str, Any]], window_info: dict[str, Any]) -> dict[str, Any]:
        message_list = self._first_node(
            nodes,
            lambda node: node["control_type"] == "ListControl"
            and (node["automation_id"] == "chat_message_list" or node["name"] == "消息"),
        )
        messages = []
        if message_list is not None:
            message_prefix = message_list["path"] + "."
            messages = [
                self._message_item(node)
                for node in nodes
                if node["path"].startswith(message_prefix) and node["depth"] == message_list["depth"] + 1
            ]

        detail_node = self._first_node(
            nodes,
            lambda node: node["class_name"] in {"mmui::ChatDetailView", "mmui::ChatMessagePage", "mmui::ChatPage"},
        )
        return {
            "available": message_list is not None,
            "title": window_info["name"],
            "path": "" if message_list is None else message_list["path"],
            "rect": None if message_list is None else message_list["rect"],
            "detail_path": None if detail_node is None else detail_node["path"],
            "messages": messages,
        }

    def _parse_input_box(self, nodes: list[dict[str, Any]]) -> dict[str, Any]:
        input_node = self._first_node(
            nodes,
            lambda node: node["control_type"] == "EditControl"
            and (node["automation_id"] == "chat_input_field" or node["class_name"] == "mmui::ChatInputField"),
        )
        if input_node is None:
            return {"available": False}

        return {
            "available": True,
            "path": input_node["path"],
            "name": input_node["name"],
            "rect": input_node["rect"],
            "enabled": input_node["enabled"],
            "focused": input_node["focused"],
        }

    def _chat_list_item(self, node: dict[str, Any]) -> dict[str, Any]:
        raw_text = node["name"]
        chat_name = node["automation_id"].removeprefix("session_item_") or self._chat_name_from_raw(raw_text)
        unread = self._unread_count(raw_text)
        return {
            "path": node["path"],
            "name": chat_name,
            "raw_text": raw_text,
            "preview": self._preview_from_raw(raw_text, chat_name),
            "unread": unread,
            "muted": "消息免打扰" in raw_text,
            "rect": node["rect"],
        }

    def _message_item(self, node: dict[str, Any]) -> dict[str, Any]:
        raw_text = node["name"]
        kind = "timestamp" if node["class_name"] == "mmui::ChatItemView" else "text"
        return {
            "path": node["path"],
            "kind": kind,
            "content": raw_text,
            "raw_text": raw_text,
            "class_name": node["class_name"],
            "rect": node["rect"],
        }

    @staticmethod
    def _first_node(nodes: list[dict[str, Any]], predicate) -> dict[str, Any] | None:
        for node in nodes:
            if predicate(node):
                return node
        return None

    @staticmethod
    def _chat_name_from_raw(raw_text: str) -> str:
        raw_text = raw_text.strip()
        if not raw_text:
            return ""
        if " [" in raw_text:
            return raw_text.split(" [", 1)[0].strip()
        return raw_text.split("  ", 1)[0].strip()

    @staticmethod
    def _unread_count(raw_text: str) -> int:
        import re

        match = re.search(r"\[(\d+)条\]", raw_text)
        if not match:
            return 0
        return int(match.group(1))

    @staticmethod
    def _preview_from_raw(raw_text: str, chat_name: str) -> str:
        preview = raw_text.removeprefix(chat_name).strip()
        if preview.startswith("[") and "条]" in preview:
            preview = preview.split("条]", 1)[1].strip()
        preview = preview.replace("消息免打扰", "").strip()
        return preview

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
            "focused": self._safe_attr(control, "HasKeyboardFocus", None),
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
