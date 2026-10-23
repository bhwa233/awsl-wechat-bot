from __future__ import annotations

import logging
import threading
import time
from contextlib import ExitStack
from typing import Any

from gateway.adapters.windows import WindowsWeChatAdapter
from gateway.console.adapters.base import BaseConsoleAdapter
from gateway.console.adapters.windows.constants import (
    _AT_POPOVER_GONE_TIMEOUT,
    _AT_POPOVER_INTERVAL,
    _AT_POPOVER_TIMEOUT,
    _AT_PREFIX_RE,
    _CHAT_SCROLL_WHEEL_TIMES,
)
from gateway.console.adapters.windows.core import WindowsUIAMixin
from gateway.console.adapters.windows.standalone import WindowsStandaloneMixin
from gateway.console.adapters.windows.state import WindowsStateMixin
from gateway.utils.voice_synthesis import synthesize_voice

logger = logging.getLogger(__name__)


class WindowsConsoleAdapter(
    WindowsStandaloneMixin,
    WindowsStateMixin,
    WindowsUIAMixin,
    BaseConsoleAdapter,
):
    def __init__(self, wechat_adapter: WindowsWeChatAdapter):
        self._wechat_adapter = wechat_adapter
        self._ui_lock = wechat_adapter.ui_operation_lock
        self._console_lock = threading.Lock()
        self._activate_window_func = wechat_adapter.activate_specific_window
        self._set_clipboard_text = wechat_adapter.set_clipboard_text_verified
        self._send_image_to_window = wechat_adapter.send_image_to_window
        self._send_file_to_window = wechat_adapter.send_file_to_window
        self._send_voice_to_window = wechat_adapter.send_voice_to_window
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

    def open_chat(self, *, path: str, max_nodes: int = 5000) -> dict[str, Any]:
        if not self._console_lock.acquire(blocking=False):
            return self._action_error("Console 正在执行其他操作")
        try:
            return self._open_chat(path=path, max_nodes=max_nodes)
        finally:
            self._console_lock.release()

    def scroll_chats(self, *, direction: str, max_nodes: int = 5000) -> dict[str, Any]:
        if direction not in {"up", "down"}:
            return self._action_error(f"无效滚动方向: {direction}")
        if not self._console_lock.acquire(blocking=False):
            return self._action_error("Console 正在执行其他操作")
        try:
            return self._scroll_chats(direction=direction, max_nodes=max_nodes)
        finally:
            self._console_lock.release()

    def _scroll_chats(self, *, direction: str, max_nodes: int) -> dict[str, Any]:
        auto = self._ensure_uia()
        with self._ui_operation_lock():
            window = self._find_main_window(auto)
            if window is None:
                return self._action_error("未找到微信主窗口")
            if not self._activate_window_locked(window):
                return self._action_error("激活微信主窗口失败", window=window)

            nodes = self._collect_nodes(window, max_depth=18, max_nodes=max_nodes)
            list_node = self._chat_list_node(nodes)
            if list_node is None:
                return self._action_error("未找到聊天列表", window=window)
            try:
                scroll = list_node["_control"].WheelUp if direction == "up" else list_node["_control"].WheelDown
                scroll(wheelTimes=_CHAT_SCROLL_WHEEL_TIMES, interval=0.03, waitTime=0.35)
            except Exception as e:
                return self._action_error(f"滚动聊天列表失败: {e}", window=window)

            return self.get_state(max_nodes=max_nodes)

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
            target_automation_id = target_info["automation_id"]
            if (
                target_info["class_name"] != "mmui::ChatSessionCell"
                and not target_automation_id.startswith("session_item_")
            ):
                return self._action_error(f"path={path} 不是聊天项", window=window)
            expected_chat_name = (
                target_automation_id.removeprefix("session_item_")
                or self._chat_name_from_raw(target_info["name"])
            )
            if not expected_chat_name:
                return self._action_error(f"聊天项缺少名称 path={path}", window=window)

            if not self._activate_window_locked(window):
                return self._action_error("激活微信主窗口失败", window=window)

            target_node = self._find_chat_session_node(
                window,
                target_automation_id=target_automation_id,
                expected_chat_name=expected_chat_name,
                max_nodes=max_nodes,
            )
            if target_node is None:
                return self._action_error(f"激活后未找到聊天项「{expected_chat_name}」", window=window)

            selection_state = self._chat_session_selection_state(target_node["_control"])
            if selection_state is None:
                current_state = self.get_state(max_nodes=max_nodes)
                chat_area = current_state.get("chat_area", {})
                input_box = current_state.get("input_box", {})
                chat_names = [
                    item["name"]
                    for item in current_state.get("chat_list", {}).get("items", [])
                ]
                if (
                    chat_area.get("available")
                    and input_box.get("available")
                    and self._chat_names_match(
                        expected_chat_name,
                        chat_area.get("title", ""),
                        chat_names,
                    )
                ):
                    selection_state = True
            if selection_state is not True:
                selected_with_pattern = self._select_chat_session(target_node["_control"])
                if selected_with_pattern:
                    selection_state = self._wait_for_chat_selection(
                        window,
                        target_automation_id=target_automation_id,
                        expected_chat_name=expected_chat_name,
                        max_nodes=max_nodes,
                        timeout=0.8,
                    )
                if selection_state is not True:
                    target_node = self._find_chat_session_node(
                        window,
                        target_automation_id=target_automation_id,
                        expected_chat_name=expected_chat_name,
                        max_nodes=max_nodes,
                    )
                    if target_node is None:
                        return self._action_error(
                            f"选择前未找到聊天项「{expected_chat_name}」",
                            window=window,
                        )
                    fresh_selection_state = self._chat_session_selection_state(
                        target_node["_control"]
                    )
                    if fresh_selection_state is not True:
                        if not selected_with_pattern or fresh_selection_state is False:
                            try:
                                target_node["_control"].Click(waitTime=0.35)
                            except Exception as e:
                                return self._action_error(f"选择聊天项失败: {e}", window=window)

            deadline = time.monotonic() + 2.0
            current_chat_name = ""
            final_selection_state = selection_state
            while True:
                result = self.get_state(max_nodes=max_nodes)
                chat_area = result.get("chat_area", {})
                input_box = result.get("input_box", {})
                chat_names = [item["name"] for item in result.get("chat_list", {}).get("items", [])]
                current_chat_name = chat_area.get("title", "")
                chat_ready = (
                    chat_area.get("available")
                    and input_box.get("available")
                    and self._chat_names_match(expected_chat_name, current_chat_name, chat_names)
                )
                if chat_ready:
                    self._focus_input(window)
                    result = self.get_state(max_nodes=max_nodes)
                    chat_area = result.get("chat_area", {})
                    input_box = result.get("input_box", {})
                    chat_names = [
                        item["name"]
                        for item in result.get("chat_list", {}).get("items", [])
                    ]
                    current_chat_name = chat_area.get("title", "")
                    if (
                        input_box.get("focused") is True
                        and self._chat_names_match(
                            expected_chat_name,
                            current_chat_name,
                            chat_names,
                        )
                    ):
                        return result
                target_node = self._find_chat_session_node(
                    window,
                    target_automation_id=target_automation_id,
                    expected_chat_name=expected_chat_name,
                    max_nodes=max_nodes,
                )
                if target_node is not None:
                    final_selection_state = self._chat_session_selection_state(
                        target_node["_control"]
                    )
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.1)
            if final_selection_state is True:
                return self._action_error(
                    f"聊天已选中但输入区未就绪: 目标「{expected_chat_name}」，"
                    f"当前「{current_chat_name or '未识别'}」",
                    window=window,
                )
            return self._action_error(
                f"打开聊天失败: 目标「{expected_chat_name}」，当前「{current_chat_name or '未识别'}」",
                window=window,
            )

    def _find_chat_session_node(
        self,
        window,
        *,
        target_automation_id: str,
        expected_chat_name: str,
        max_nodes: int,
    ) -> dict[str, Any] | None:
        nodes = self._collect_nodes(window, max_depth=18, max_nodes=max_nodes)
        return self._first_node(
            nodes,
            lambda node: (
                node["class_name"] == "mmui::ChatSessionCell"
                or node["automation_id"].startswith("session_item_")
            )
            and (
                node["automation_id"] == target_automation_id
                if target_automation_id
                else self._chat_name_from_raw(node["name"]) == expected_chat_name
            ),
        )

    def _chat_session_selection_state(self, control) -> bool | None:
        pattern = self._safe_call(control, "GetSelectionItemPattern", default=None)
        if pattern is None:
            return None
        selected = self._safe_attr(pattern, "IsSelected", None)
        return selected if isinstance(selected, bool) else None

    def _select_chat_session(self, control) -> bool:
        pattern = self._safe_call(control, "GetSelectionItemPattern", default=None)
        if pattern is None:
            return False
        try:
            pattern.Select()
        except Exception as e:
            logger.debug("SelectionItemPattern.Select 失败,准备回退 Click: %s", e)
            return False
        return True

    def _wait_for_chat_selection(
        self,
        window,
        *,
        target_automation_id: str,
        expected_chat_name: str,
        max_nodes: int,
        timeout: float,
    ) -> bool | None:
        deadline = time.monotonic() + timeout
        selection_state = None
        while True:
            target_node = self._find_chat_session_node(
                window,
                target_automation_id=target_automation_id,
                expected_chat_name=expected_chat_name,
                max_nodes=max_nodes,
            )
            if target_node is not None:
                selection_state = self._chat_session_selection_state(target_node["_control"])
                if selection_state is True:
                    return True
            if time.monotonic() >= deadline:
                return selection_state
            time.sleep(0.05)

    def _send_text_message(
        self, *, text: str, chat_name: str, max_nodes: int = 5000, reply_to_message: str | None = None,
        mention_all: bool = False,
    ) -> dict[str, Any]:
        auto = self._ensure_uia()
        window = self._find_main_window(auto)
        if window is None:
            return self._action_error("未找到微信主窗口")
        with self._ui_operation_lock(), ExitStack() as cleanup:
            if not self._activate_window_locked(window):
                return self._action_error("激活微信主窗口失败", window=window)
            current, chat_names = self._current_chat_context(window)
            if not self._chat_names_match(chat_name, current, chat_names):
                return self._chat_name_error(chat_name, current, window)
            self._wechat_adapter.clear_reply(window)

            target = self._focus_input(window)
            if target is None:
                return self._action_error("未找到聊天输入框", window=window)

            input_id = None
            sent_token = None
            try:
                target.SendKeys("{CTRL}a{DELETE}", waitTime=0.2)
                if cleanup.enter_context(self._wechat_adapter.reply_context(window, reply_to_message)):
                    target = self._focus_input(window)
                    if target is None:
                        return self._action_error("引用后未找到输入框", window=window)

                if mention_all:
                    input_id = tuple(target.GetRuntimeId())
                    name = self._wechat_adapter.select_mention_all(window, target)
                    if not self._chat_names_match(chat_name, *self._current_chat_context(window)):
                        raise RuntimeError("Chat changed before pasting the @all message")
                    sent_text = f"@{name} {text}"
                at_names, remaining = ([], text) if mention_all else self._parse_at_prefix(text)
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
                if mention_all:
                    keys = {self._wechat_adapter._message_state_key(control)
                            for control in self._standalone_windows(auto) if control.Name == chat_name}
                    if len(keys) == 1:
                        sent_token = self._wechat_adapter._sent_messages.record(keys.pop(), sent_text)
                    if (not self._chat_names_match(chat_name, *self._current_chat_context(window))
                            or not target.HasKeyboardFocus or tuple(target.GetRuntimeId()) != input_id):
                        raise RuntimeError("Chat or input focus changed before sending @all")
                target.SendKeys("{ENTER}", waitTime=0.2)
            except Exception as e:
                if sent_token is not None:
                    self._wechat_adapter._sent_messages.discard(sent_token)
                if (mention_all and input_id is not None
                        and self._chat_names_match(chat_name, *self._current_chat_context(window))):
                    self._wechat_adapter.clear_mention_all_draft(window, target, input_id)
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
        reply_to_message: str | None = None,
        mention_all: bool = False,
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
                reply_to_message=reply_to_message,
                mention_all=mention_all,
            )
        except Exception as e:
            return self._action_error(f"发送消息失败: {e}")
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
        reply_to_message: str | None = None,
        mention_all: bool = False,
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

            if message_type != "text":
                self._wechat_adapter.clear_reply(window)

        if message_type == "text":
            return self._send_text_message(text=message, chat_name=chat_name, max_nodes=max_nodes,
                                           reply_to_message=reply_to_message, mention_all=mention_all)

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
        if any(name.casefold() in {"所有人", "all"} for name in names):
            return [], text
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
