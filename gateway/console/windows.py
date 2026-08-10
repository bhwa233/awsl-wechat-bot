"""Windows 微信主窗口 UIA 调试信息."""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_WECHAT_WINDOW_CLASSES = {"mmui::MainWindow", "mmui::ChatSingleWindow", "WeChatMainWndForPC"}
_MAIN_WINDOW_CLASSES = {"mmui::MainWindow", "WeChatMainWndForPC"}
_MAIN_WINDOW_TITLES = {"微信", "WeChat", "Weixin"}

_AT_PREFIX_RE = re.compile(r'^((?:@[^\s@,，;；:：]+\s+)+)(.*)', re.DOTALL)
_AT_POPOVER_TIMEOUT = 0.5
_AT_POPOVER_GONE_TIMEOUT = 0.3
_AT_POPOVER_INTERVAL = 0.05


class WindowsConsoleDebugService:
    """读取 Windows 微信主窗口 UIA 树."""

    def __init__(
        self,
        *,
        ui_operation_lock: threading.Lock | None = None,
        console_operation_lock: threading.Lock | None = None,
        activate_window: Callable[[Any], None] | None = None,
        set_clipboard_text: Callable[[str], None] | None = None,
        send_image_to_window: Callable[[Any, str], bool] | None = None,
        send_file_to_window: Callable[[Any, str, str], bool] | None = None,
        send_voice_to_window: Callable[..., bool] | None = None,
        synthesize_voice: Callable[[str], tuple[str, str]] | None = None,
    ):
        self._ui_lock = ui_operation_lock or threading.Lock()
        self._console_lock = console_operation_lock or threading.Lock()
        self._activate_window_func = activate_window
        self._set_clipboard_text = set_clipboard_text
        self._send_image_to_window = send_image_to_window
        self._send_file_to_window = send_file_to_window
        self._send_voice_to_window = send_voice_to_window
        self._synthesize_voice = synthesize_voice
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

        nodes = self._collect_nodes(window, max_depth=18, max_nodes=max_nodes)
        chat_list = self._parse_chat_list(nodes)
        return {
            "available": True,
            "platform": "windows",
            "message": "",
            "window": info,
            "chat_list": chat_list,
            "chat_area": self._parse_chat_area(nodes, chat_list["items"]),
            "input_box": self._parse_input_box(nodes),
            "node_count": len(nodes),
        }

    def dump_window(
        self,
        *,
        handle: int,
        max_depth: int = 5,
        path: str = "",
        max_nodes: int = 2000,
        include_patterns: bool = False,
    ) -> dict[str, Any]:
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
                include_patterns=include_patterns,
            ),
            "node_count": counter["count"],
        }

    def open_chat(self, *, path: str, max_nodes: int = 5000) -> dict[str, Any]:
        if not self._console_lock.acquire(blocking=False):
            return self._action_error("Console 正在执行其他操作")
        try:
            return self._open_chat(path=path, max_nodes=max_nodes)
        finally:
            self._console_lock.release()

    def _open_chat(self, *, path: str, max_nodes: int) -> dict[str, Any]:
        auto = self._ensure_uia()
        with self._ui_operation_lock():
            window = self._find_main_window(auto)
            if window is None:
                return self._action_error("未找到微信主窗口")

            try:
                path_indexes = self._parse_path(path)
            except ValueError:
                return self._action_error(f"无效 path={path}", window=window)

            target = self._find_by_path(window, path_indexes)
            if target is None:
                return self._action_error(f"未找到聊天项 path={path}", window=window)
            target_info = self._control_info(target)
            expected_chat_name = (
                target_info["automation_id"].removeprefix("session_item_")
                or self._chat_name_from_raw(target_info["name"])
            )

            if not self._activate_window_locked(window):
                return self._action_error("激活微信主窗口失败", window=window)

            target = self._find_by_path(window, path_indexes)
            if target is None:
                return self._action_error(f"激活后未找到聊天项 path={path}", window=window)

            try:
                target.GetSelectionItemPattern().Select()
            except Exception as e:
                return self._action_error(f"选择聊天项失败: {e}", window=window)

            time.sleep(0.35)
            self._focus_input(window)
            result = self.get_state(max_nodes=max_nodes)
            chat_area = result.get("chat_area", {})
            input_box = result.get("input_box", {})
            chat_names = [item["name"] for item in result.get("chat_list", {}).get("items", [])]
            current_chat_name = chat_area.get("title", "")
            if (
                not chat_area.get("available")
                or not input_box.get("available")
                or not self._chat_names_match(expected_chat_name, current_chat_name, chat_names)
            ):
                return self._action_error(
                    f"打开聊天失败: 目标「{expected_chat_name}」，当前「{current_chat_name or '未识别'}」",
                    window=window,
                )
            return result

    def _send_text_message(self, *, text: str, max_nodes: int = 5000) -> dict[str, Any]:
        auto = self._ensure_uia()
        with self._ui_operation_lock():
            window = self._find_main_window(auto)
            if window is None:
                return self._action_error("未找到微信主窗口")
            if not self._activate_window_locked(window):
                return self._action_error("激活微信主窗口失败", window=window)

            target = self._focus_input(window)
            if target is None:
                return self._action_error("未找到聊天输入框", window=window)

            try:
                target.SendKeys("{CTRL}a{DELETE}", waitTime=0.2)

                at_names, remaining = self._parse_at_prefix(text)
                at_ok = True
                try:
                    baseline = self._count_at_popovers(auto, window)
                    for name in at_names:
                        target.SendKeys('@', waitTime=0.05)
                        if not self._wait_at_popover(auto, window, baseline):
                            logger.warning("[console.send_text] @ popover 未出现: %s", name)
                            at_ok = False
                            break
                        self._set_clipboard_text_verified(auto, name)
                        target.SendKeys('{CTRL}v', waitTime=0.05)
                        if not self._wait_at_popover_candidate(auto, window):
                            logger.warning("[console.send_text] @ popover 无候选: %s", name)
                            at_ok = False
                            break
                        target.SendKeys('{ENTER}', waitTime=0.05)
                        self._wait_at_popover_gone(auto, window, baseline)
                except Exception as e:
                    logger.warning("[console.send_text] @ 流程异常，fallback 原文发送: %s", e)
                    at_ok = False
                if not at_ok:
                    target.SendKeys('{CTRL}a{DELETE}', waitTime=0.1)
                    remaining = text

                if remaining:
                    self._set_clipboard_text_verified(auto, remaining)
                    target.SendKeys("{CTRL}v", waitTime=0.2)
                target.SendKeys("{ENTER}", waitTime=0.2)
            except Exception as e:
                return self._action_error(f"发送文本失败: {e}", window=window)

            time.sleep(0.35)
            return self.get_state(max_nodes=max_nodes)

    def send_message(
        self,
        *,
        message_type: str,
        message: str,
        filename: str = "",
        chat_name: str,
        max_nodes: int = 5000,
    ) -> dict[str, Any]:
        if not self._console_lock.acquire(blocking=False):
            return self._action_error("Console 正在执行其他操作")
        try:
            return self._send_message(
                message_type=message_type,
                message=message,
                filename=filename,
                chat_name=chat_name,
                max_nodes=max_nodes,
            )
        finally:
            self._console_lock.release()

    def _send_message(
        self,
        *,
        message_type: str,
        message: str,
        filename: str,
        chat_name: str,
        max_nodes: int,
    ) -> dict[str, Any]:
        if message_type == "voice":
            if not self._send_voice_to_window or not self._synthesize_voice:
                return self._action_error("当前 Console 未配置语音发送能力")
            try:
                file_base64, filename = self._synthesize_voice(message)
            except Exception as e:
                return self._action_error(f"生成语音失败: {e}")
            message = file_base64

        auto = self._ensure_uia()
        window = self._find_main_window(auto)
        if window is None:
            return self._action_error("未找到微信主窗口")
        with self._ui_operation_lock():
            if not self._activate_window_locked(window):
                return self._action_error("激活微信主窗口失败", window=window)
            current_chat_name, chat_names = self._current_chat_context(window)
            if not self._chat_names_match(chat_name, current_chat_name, chat_names):
                return self._chat_name_error(chat_name, current_chat_name, window)

        if message_type == "text":
            return self._send_text_message(text=message, max_nodes=max_nodes)

        if message_type == "image":
            if not self._send_image_to_window:
                return self._action_error("当前 Console 未配置图片发送能力", window=window)
            ok = self._send_image_to_window(window, message)
            error_message = "发送图片失败"
        elif message_type == "file":
            if not filename:
                return self._action_error("文件消息缺少 filename", window=window)
            if not self._send_file_to_window:
                return self._action_error("当前 Console 未配置文件发送能力", window=window)
            ok = self._send_file_to_window(window, message, filename)
            error_message = "发送文件失败"
        elif message_type in {"voice", "voice_file"}:
            if not filename:
                return self._action_error("语音文件消息缺少 filename", window=window)
            if not self._send_voice_to_window:
                return self._action_error("当前 Console 未配置语音发送能力", window=window)
            ok = self._send_voice_to_window(window, message, filename)
            error_message = "发送 TTS 语音失败" if message_type == "voice" else "发送语音文件失败"
        else:
            return self._action_error(f"不支持的消息类型: {message_type}", window=window)

        if not ok:
            return self._action_error(error_message, window=window)

        time.sleep(0.35)
        return self.get_state(max_nodes=max_nodes)

    @staticmethod
    def _parse_at_prefix(text: str) -> tuple[list[str], str]:
        m = _AT_PREFIX_RE.match(text)
        if not m:
            return [], text
        prefix, body = m.group(1), m.group(2)
        names = [tok[1:] for tok in prefix.split() if tok.startswith('@')]
        return names, body

    @staticmethod
    def _count_at_popovers(auto, window) -> int:
        try:
            return sum(
                1 for ctrl, _depth in auto.WalkControl(window, maxDepth=10)
                if ctrl.ClassName == 'mmui::XPopover' and ctrl.Exists(0)
            )
        except Exception as e:
            logger.debug("_count_at_popovers 异常: %s", e)
            return 0

    @staticmethod
    def _at_popover_has_candidate(auto, window) -> bool:
        try:
            for ctrl, _depth in auto.WalkControl(window, maxDepth=10):
                if ctrl.ClassName != 'mmui::XPopover' or not ctrl.Exists(0):
                    continue
                for sub, _d in auto.WalkControl(ctrl, maxDepth=8):
                    if sub.ControlTypeName == 'ListItemControl' and sub.Exists(0):
                        return True
            return False
        except Exception as e:
            logger.debug("_at_popover_has_candidate 异常: %s", e)
            return False

    def _wait_at_popover(self, auto, window, baseline: int) -> bool:
        deadline = time.monotonic() + _AT_POPOVER_TIMEOUT
        while True:
            if self._count_at_popovers(auto, window) > baseline:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(_AT_POPOVER_INTERVAL)

    def _wait_at_popover_candidate(self, auto, window) -> bool:
        deadline = time.monotonic() + _AT_POPOVER_TIMEOUT
        while True:
            if self._at_popover_has_candidate(auto, window):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(_AT_POPOVER_INTERVAL)

    def _wait_at_popover_gone(self, auto, window, baseline: int) -> bool:
        deadline = time.monotonic() + _AT_POPOVER_GONE_TIMEOUT
        while True:
            if self._count_at_popovers(auto, window) <= baseline:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(_AT_POPOVER_INTERVAL)

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
        hwnd = self._safe_attr(window, "NativeWindowHandle", 0)
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
            input_control.Click(waitTime=0)
        except Exception as e:
            logger.debug("点击主窗口输入框失败: %s", e)
        try:
            input_control.SetFocus()
        except Exception as e:
            logger.debug("聚焦主窗口输入框失败: %s", e)
        time.sleep(0.15)
        return input_control

    def _find_input_control(self, window):
        nodes = self._collect_nodes(window, max_depth=18, max_nodes=5000)
        input_node = self._first_node(
            nodes,
            lambda node: node["control_type"] == "EditControl"
            and (node["automation_id"] == "chat_input_field" or node["class_name"] == "mmui::ChatInputField"),
        )
        return None if input_node is None else input_node["_control"]

    def _current_chat_context(self, window) -> tuple[str, list[str]]:
        nodes = self._collect_nodes(window, max_depth=18, max_nodes=5000)
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

    def _parse_chat_area(
        self,
        nodes: list[dict[str, Any]],
        chat_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
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
        title_node = self._chat_title_node(nodes)
        raw_title = "" if title_node is None else str(title_node["name"]).strip()
        chat_names = [item["name"] for item in chat_items]
        if raw_title in chat_names:
            title = raw_title
        else:
            title = next(
                (name for name in chat_names if self._chat_names_match(name, raw_title, chat_names)),
                raw_title,
            )
        return {
            "available": message_list is not None,
            "title": title,
            "path": "" if message_list is None else message_list["path"],
            "rect": None if message_list is None else message_list["rect"],
            "detail_path": None if detail_node is None else detail_node["path"],
            "messages": messages,
        }

    @staticmethod
    def _chat_title_node(nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
        return WindowsConsoleDebugService._first_node(
            nodes,
            lambda node: node["control_type"] == "TextControl"
            and node["automation_id"].endswith("big_title_line_h_view"),
        )

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
        preview, message_time = self._preview_and_time_from_raw(raw_text, chat_name)
        return {
            "path": node["path"],
            "name": chat_name,
            "raw_text": raw_text,
            "preview": preview,
            "time": message_time,
            "unread": unread,
            "muted": "消息免打扰" in raw_text,
            "rect": node["rect"],
        }

    def _message_item(self, node: dict[str, Any]) -> dict[str, Any]:
        raw_text = node["name"]
        kind = "timestamp" if node["class_name"] == "mmui::ChatItemView" else "text"
        content, quote = self._message_content_and_quote(raw_text)
        return {
            "path": node["path"],
            "kind": kind,
            "content": content,
            "quote": quote,
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
    def _preview_and_time_from_raw(raw_text: str, chat_name: str) -> tuple[str, str]:
        preview = raw_text.removeprefix(chat_name).strip()
        if preview.startswith("[") and "条]" in preview:
            preview = preview.split("条]", 1)[1].strip()
        preview = preview.replace("消息免打扰", "").strip()
        lines = [line.strip() for line in preview.splitlines()]
        while lines and not lines[-1]:
            lines.pop()
        if not lines:
            return "", ""

        if not WindowsConsoleDebugService._looks_like_chat_time(lines[-1]):
            return "\n".join(lines).strip(), ""

        message_time = lines.pop()
        return "\n".join(lines).strip(), message_time

    @staticmethod
    def _looks_like_chat_time(value: str) -> bool:
        import re

        text = value.strip()
        if not text:
            return False
        if re.fullmatch(r"(上午|下午|晚上|凌晨|中午)?\s*\d{1,2}:\d{2}", text):
            return True
        if text in {"昨天", "前天"}:
            return True
        if re.fullmatch(r"(星期|周)[一二三四五六日天]", text):
            return True
        return bool(re.fullmatch(r"(\d{4}[/-])?\d{1,2}[/-]\d{1,2}", text))

    @staticmethod
    def _message_content_and_quote(raw_text: str) -> tuple[str, dict[str, str] | None]:
        import re

        text = raw_text.strip()
        match = re.search(r"引用\s+(.+?)\s+的消息\s*[:：]\s*(.+)$", text, re.S)
        if match is None:
            return text, None

        content = text[: match.start()].strip()
        quote_sender = match.group(1).strip()
        quote_content = match.group(2).strip()
        if not quote_sender or not quote_content:
            return text, None

        return content, {
            "sender": quote_sender,
            "content": quote_content,
        }

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

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if value is None or isinstance(value, str | int | float | bool):
            return value
        if isinstance(value, (list, tuple)):
            return [WindowsConsoleDebugService._jsonable(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): WindowsConsoleDebugService._jsonable(item)
                for key, item in value.items()
            }
        return repr(value)
