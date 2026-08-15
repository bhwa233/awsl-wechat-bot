from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any

from gateway.console.adapters.windows.constants import (
    _MAIN_WINDOW_CLASSES,
    _MAIN_WINDOW_TITLES,
    _STATE_MAX_DEPTH,
    _WECHAT_WINDOW_CLASSES,
)

logger = logging.getLogger(__name__)


class WindowsUIAMixin:
    def _ensure_uia(self):
        import uiautomation as auto

        tid = threading.get_ident()
        if tid not in self._thread_inits:
            self._thread_inits[tid] = auto.UIAutomationInitializerInThread()
            logger.debug("console 线程 %s UIA 初始化完成", tid)
        return auto

    def _ui_operation_lock(self):
        return self._ui_lock

    def _set_clipboard_text_verified(self, auto, text: str) -> None:
        if self._set_clipboard_text is not None:
            self._set_clipboard_text(text)
            return
        auto.SetClipboardText(text)
        if auto.GetClipboardText() != text:
            raise RuntimeError("剪贴板内容校验失败")

    def _activate_window_locked(self, window) -> bool:
        """激活微信窗口.

        调用方必须持有 self._ui_lock, 保证和 Gateway 原有 Windows 发送路径共用同一把前台 UI 操作锁.
        """
        if self._activate_window_func is not None:
            try:
                self._activate_window_func(window)
            except Exception as e:
                logger.debug("适配器激活微信主窗口失败,继续尝试 console 激活: %s", e)

        if self._activate_window_by_hwnd_locked(window):
            time.sleep(0.3)
            return True

        try:
            if hasattr(window, "ShowWindow"):
                window.ShowWindow(1)
        except Exception:
            pass
        try:
            if hasattr(window, "Restore"):
                window.Restore()
        except Exception:
            pass
        try:
            window.SetForeground()
            time.sleep(0.3)
            return True
        except Exception as e:
            logger.warning("激活微信主窗口失败: %s", e)
            return False

    def _activate_window_by_hwnd_locked(self, window) -> bool:
        hwnd = self._safe_int_attr(window, "NativeWindowHandle")
        if not hwnd:
            return False

        try:
            import win32con
            import win32gui

            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_TOPMOST,
                0,
                0,
                0,
                0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE,
            )
            win32gui.SetWindowPos(
                hwnd,
                win32con.HWND_NOTOPMOST,
                0,
                0,
                0,
                0,
                win32con.SWP_NOMOVE | win32con.SWP_NOSIZE,
            )
            win32gui.BringWindowToTop(hwnd)
            win32gui.SetForegroundWindow(hwnd)
            return True
        except Exception as e:
            logger.debug("通过 hwnd 激活微信主窗口失败: %s", e)
            return False

    def _focus_input(self, window):
        input_control = self._find_input_control(window)
        if input_control is None:
            return None
        try:
            input_control.SetFocus()
        except Exception as e:
            logger.debug("聚焦主窗口输入框失败: %s", e)
        time.sleep(0.05)
        if self._safe_attr(input_control, "HasKeyboardFocus", False) is True:
            return input_control
        try:
            input_control.Click(waitTime=0)
        except Exception as e:
            logger.debug("点击主窗口输入框失败: %s", e)
        try:
            input_control.SetFocus()
        except Exception as e:
            logger.debug("点击后聚焦主窗口输入框失败: %s", e)
        time.sleep(0.15)
        return input_control

    def _find_input_control(self, window):
        nodes = self._collect_nodes(window, max_depth=_STATE_MAX_DEPTH, max_nodes=5000)
        input_node = self._first_node(
            nodes,
            lambda node: node["control_type"] == "EditControl"
            and (node["automation_id"] == "chat_input_field" or node["class_name"] == "mmui::ChatInputField"),
        )
        return None if input_node is None else input_node["_control"]

    def _current_chat_context(self, window) -> tuple[str, list[str]]:
        nodes = self._collect_nodes(window, max_depth=_STATE_MAX_DEPTH, max_nodes=5000)
        title_node = self._chat_title_node(nodes)
        current = "" if title_node is None else str(title_node["name"]).strip()
        chat_names = [item["name"] for item in self._parse_chat_list(nodes)["items"]]
        return current, chat_names

    @staticmethod
    def _chat_names_match(target: str, current: str, chat_names: list[str]) -> bool:
        if current == target:
            return True
        if current in chat_names:
            return False
        return bool(
            re.fullmatch(rf"{re.escape(target)}\s*(?:\(\d+\)|（\d+）)", current)
        )

    def _chat_name_error(self, target: str, current: str, window) -> dict[str, Any]:
        current_label = current or "未识别"
        return self._action_error(
            f"聊天名称不匹配: 选中「{target}」，当前「{current_label}」",
            window=window,
        )

    def _action_error(self, message: str, window=None) -> dict[str, Any]:
        return {
            "available": False,
            "platform": "windows",
            "message": message,
            "window": None if window is None else self._control_info(window),
        }

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

    def _dump_control(
        self,
        control,
        *,
        max_depth: int,
        depth: int,
        path: tuple[int, ...],
        counter: dict[str, int],
        max_nodes: int,
        include_patterns: bool,
    ) -> dict[str, Any]:
        counter["count"] += 1
        node = self._control_info(control)
        if include_patterns:
            node["extra"] = self._extra_control_info(control)
            node["patterns"] = self._pattern_info(control)
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
                    include_patterns=include_patterns,
                )
            )
        return node

    def _extra_control_info(self, control) -> dict[str, Any]:
        fields = (
            "LocalizedControlType",
            "HelpText",
            "ItemStatus",
            "ItemType",
            "AccessKey",
            "AcceleratorKey",
            "AriaRole",
            "AriaProperties",
            "IsContentElement",
            "IsControlElement",
            "IsKeyboardFocusable",
            "IsPassword",
            "LabeledBy",
        )
        return {
            name: self._jsonable(self._safe_attr(control, name, None))
            for name in fields
        }

    def _pattern_info(self, control) -> dict[str, Any]:
        pattern_specs = {
            "legacy_iaccessible": (
                "GetLegacyIAccessiblePattern",
                ("Name", "Value", "Description", "Role", "State", "DefaultAction", "Help", "KeyboardShortcut", "ChildId"),
            ),
            "value": ("GetValuePattern", ("Value", "IsReadOnly")),
            "selection_item": ("GetSelectionItemPattern", ("IsSelected", "SelectionContainer")),
            "toggle": ("GetTogglePattern", ("ToggleState",)),
            "range_value": ("GetRangeValuePattern", ("Value", "Minimum", "Maximum", "SmallChange", "LargeChange", "IsReadOnly")),
            "expand_collapse": ("GetExpandCollapsePattern", ("ExpandCollapseState",)),
            "text": ("GetTextPattern", ("DocumentRange", "SupportedTextSelection")),
        }

        patterns: dict[str, Any] = {}
        for name, (method, fields) in pattern_specs.items():
            pattern = self._safe_call(control, method, default=None)
            if pattern is None:
                continue
            values = {
                field: self._jsonable(self._safe_attr(pattern, field, None))
                for field in fields
            }
            patterns[name] = values
        return patterns

    def _control_info(self, control) -> dict[str, Any]:
        return {
            "name": self._safe_text_attr(control, "Name"),
            "class_name": self._safe_text_attr(control, "ClassName"),
            "control_type": self._safe_text_attr(control, "ControlTypeName"),
            "automation_id": self._safe_text_attr(control, "AutomationId"),
            "framework_id": self._safe_text_attr(control, "FrameworkId"),
            "process_id": self._safe_int_attr(control, "ProcessId"),
            "native_window_handle": self._safe_int_attr(control, "NativeWindowHandle"),
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

    @classmethod
    def _safe_text_attr(cls, obj, name: str) -> str:
        value = cls._safe_attr(obj, name)
        return value if isinstance(value, str) else ""

    @classmethod
    def _safe_int_attr(cls, obj, name: str) -> int | None:
        value = cls._safe_attr(obj, name)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError, OverflowError):
            return None

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

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if value is None or isinstance(value, str | int | float | bool):
            return value
        if isinstance(value, (list, tuple)):
            return [WindowsUIAMixin._jsonable(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): WindowsUIAMixin._jsonable(item)
                for key, item in value.items()
            }
        return repr(value)
