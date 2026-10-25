"""Linux 微信适配器.

接收消息通过 Chatlog 或 AT-SPI 读取,发送通过 X11 窗口激活和剪贴板完成.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unicodedata
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import ClassVar

import httpx
from pydantic import ValidationError

from common.config import config
from common.models import ReceivedMessage
from gateway.adapters.base import BaseWeChatAdapter, DiscoveredWindow
from gateway.models import GroupDebugInfo
from gateway.utils.linux_accessibility import LinuxAccessibility, chat_title, input_node
from gateway.utils.linux_ui_lock import LinuxUILock

logger = logging.getLogger(__name__)

_MAIN_WINDOW_TITLES = {"微信", "WeChat", "Weixin"}
_WECHAT_CLASS = "wechat.wechat"
_VOICE_MESSAGE_RE = re.compile(r'Audio\d+"sec(?:Unplayed)?')
_AT_PREFIX_RE = re.compile(r"^((?:@[^\s@,\uff0c;\uff1b:\uff1a]+\s+)+)(.*)", re.DOTALL)


class _ChatlogMessage(ReceivedMessage):
    seq: int
    time: str
    msg_type: int
    is_self: bool
    server_id: int = 0
    local_id: int = 0


class LinuxGroupDebugInfo(GroupDebugInfo):
    window_id: int
    chat_id: str | None = None


@dataclass
class LinuxWeChatWindow:
    title: str
    window_id: str
    talker: str | None = None
    expected_chat: str | None = None

    def Exists(self, timeout: float = 0.5) -> bool:
        env = _x11_env()
        result = subprocess.run(
            ["xdotool", "getwindowname", self.window_id],
            capture_output=True,
            text=True,
            timeout=timeout + 1,
            env=env,
            check=False,
        )
        return result.returncode == 0 and result.stdout.strip() == self.title


def _x11_env() -> dict[str, str]:
    env = os.environ.copy()
    if not env.get("DISPLAY"):
        env["DISPLAY"] = config.LINUX_WECHAT_DISPLAY
    if not env.get("XAUTHORITY") and config.LINUX_WECHAT_XAUTHORITY:
        env["XAUTHORITY"] = os.path.expanduser(config.LINUX_WECHAT_XAUTHORITY)
    return env


class LinuxWeChatAdapter(BaseWeChatAdapter):
    FILE_SEND_CLEANUP_DELAY = 180.0
    SUPPORTS_MENTION_ALL = True
    _TEMP_CLEANUP_DELAY: ClassVar[float] = 15.0
    _CHATLOG_RETRY_INTERVAL: ClassVar[float] = 5.0

    def __init__(self) -> None:
        self._atspi_readers = {}
        self._sent_messages = LinuxSentMessages()
        self._send_lock = LinuxUILock()
        self.accessibility = LinuxAccessibility()
        self._chatlog = httpx.Client(
            base_url=self._chatlog_base_url(),
            headers={"Authorization": f"Bearer {config.HTTP_API_TOKEN}"} if config.HTTP_API_TOKEN else {},
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
        )
        self._group_cache_at = 0.0
        self._group_cache_retry_at = 0.0
        self._title_to_talker: dict[str, str] = {}
        self._ambiguous_titles: set[str] = set()
        self._talker_to_title: dict[str, str] = {}
        self._last_seq: dict[str, int] = {}
        self._seen_messages: dict[str, set[tuple]] = {}
        self._seen_lock = threading.Lock()
        self.SUPPORTS_VOICE = all(shutil.which(command) for command in ("pactl", "paplay"))
        self._ensure_tools()

    @property
    def ui_operation_lock(self):
        return self._ui_operation()

    @contextlib.contextmanager
    def _ui_operation(self):
        with self._send_lock:
            path = self._recording_marker_path()
            if path.exists():
                window = LinuxWeChatWindow(**json.loads(path.read_text())["window"])
                if not self._finish_recording(window, cancel=True, cleanup=True):
                    raise RuntimeError("微信录音尚未确认结束,暂停 UI 操作; 请取消录音后重试")
            yield

    @staticmethod
    def _recording_marker_path():
        return Path.home() / ".cache" / "awsl-wechat-gateway" / "pending-recording.json"

    def _mark_recording(self, window, source, previous=None):
        path = self._recording_marker_path()
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temp = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "w") as file:
                json.dump({"window": asdict(window), "audio_source": source, "previous_source": previous}, file, ensure_ascii=False)
            os.replace(temp, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp)

    def _recording_stopped(self, window):
        from gateway.utils.linux_voice import wechat_microphone_streams

        try:
            if wechat_microphone_streams():
                return False
            if not window.Exists(.2):
                return True
            nodes = self.window_nodes(window)
            buttons = {n["name"] for n in nodes if n["role"] == "push button"}
            return bool(buttons & {"Send Voice", "发送语音"}) and not buttons & {"Cancel", "取消", "Send voice message", "发送语音消息"}
        except Exception as exc:
            logger.warning("无法确认微信录音已结束: %s", exc)
            return False

    def _finish_recording(self, window, *, cancel, cleanup=False):
        if cancel:
            try:
                if not self._recording_stopped(window):
                    self._voice_button(window, {"Cancel", "取消"}, require_active=False)
            except Exception as exc:
                logger.warning("取消微信录音失败: %s", exc)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if self._recording_stopped(window):
                path = self._recording_marker_path()
                if cleanup and path.exists():
                    from gateway.utils.linux_voice import cleanup_microphone

                    state = json.loads(path.read_text())
                    cleanup_microphone(state["audio_source"], state.get("previous_source"))
                path.unlink(missing_ok=True)
                return True
            time.sleep(.1)
        return False

    def window_nodes(self, window: LinuxWeChatWindow, *, max_nodes=5000) -> list[dict]:
        geometry = self._window_geometry(window.window_id)
        if geometry is None:
            raise RuntimeError("无法读取目标窗口坐标")
        frame = self.accessibility.frame(window.title, geometry)
        return self.accessibility.nodes(frame, max_nodes=max_nodes)

    def list_windows(self, *, include_main=False) -> list[LinuxWeChatWindow]:
        result = self._run_x11(["wmctrl", "-lx"], timeout=5)
        if result.returncode != 0:
            raise RuntimeError("无法枚举微信窗口")
        windows = []
        for line in result.stdout.splitlines():
            parts = line.split(None, 4)
            if len(parts) != 5:
                continue
            window_id, _, window_class, _, title = parts
            if window_class != _WECHAT_CLASS or not title or (not include_main and title in _MAIN_WINDOW_TITLES):
                continue
            talker = self._title_to_talker.get(title)
            windows.append(LinuxWeChatWindow(title, window_id, talker))
        return windows

    def close(self) -> None:
        self._chatlog.close()

    def select_new_messages(
        self,
        window: LinuxWeChatWindow,
        messages: list[ReceivedMessage],
        *,
        initialize: bool = False,
    ) -> list[ReceivedMessage]:
        return [] if initialize else messages

    def _ensure_tools(self) -> None:
        missing = [cmd for cmd in ("wmctrl", "xdotool", "xclip", "xwininfo") if not shutil.which(cmd)]
        if missing:
            raise RuntimeError(f"Linux 微信适配器缺少系统工具: {', '.join(missing)}")

    def _run_x11(self, args: list[str], *, input_text: str | None = None, timeout: float = 10.0) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=_x11_env(),
            check=False,
        )

    @staticmethod
    def _chatlog_base_url() -> str:
        host = config.CHATLOG_HTTP_HOST if config.CHATLOG_HTTP_HOST != "0.0.0.0" else "127.0.0.1"
        return f"http://{host}:{config.CHATLOG_HTTP_PORT}"

    def _refresh_group_cache(self, *, force: bool = False) -> None:
        now = time.time()
        if now < self._group_cache_retry_at:
            return
        if not force and now - self._group_cache_at < config.LINUX_CHATLOG_GROUP_CACHE_TTL:
            return

        try:
            response = self._chatlog.get("/api/chatlog/groups", params={"limit": 0})
            response.raise_for_status()
            groups = response.json()
        except httpx.HTTPError as exc:
            logger.warning("Linux chatlog 群列表读取失败: %s", exc)
            self._group_cache_retry_at = now + self._CHATLOG_RETRY_INTERVAL
            return

        title_to_talkers: dict[str, set[str]] = {}
        talker_to_title: dict[str, str] = {}
        for group in groups:
            username = group.get("username", "")
            display_name = group.get("display_name") or group.get("remark") or group.get("nick_name")
            if username and display_name:
                talker_to_title[username] = display_name
            names = {
                username,
                group.get("display_name", ""),
                group.get("remark", ""),
                group.get("nick_name", ""),
            }
            for name in names:
                if name and username:
                    title_to_talkers.setdefault(name, set()).add(username)

        self._title_to_talker = {name: next(iter(ids)) for name, ids in title_to_talkers.items() if len(ids) == 1}
        self._ambiguous_titles = {name for name, ids in title_to_talkers.items() if len(ids) > 1}
        self._talker_to_title = talker_to_title
        self._group_cache_at = now
        self._group_cache_retry_at = 0.0

    def find_all_wechat_windows(self) -> list[DiscoveredWindow]:
        if config.LINUX_MESSAGE_READER == "chatlog":
            self._refresh_group_cache()
        windows = self.list_windows()
        counts: dict[str, int] = {}
        for window in windows:
            counts[window.title] = counts.get(window.title, 0) + 1
        return [
            DiscoveredWindow(
                title=window.title,
                window=window,
                debug_info=LinuxGroupDebugInfo(window_id=int(window.window_id, 16), chat_id=window.talker),
            )
            for window in windows if counts[window.title] == 1
        ]

    def get_messages_from_window(self, window: LinuxWeChatWindow) -> list[ReceivedMessage]:
        if config.LINUX_MESSAGE_READER == "accessibility":
            key = window.window_id
            if key not in self._atspi_readers:
                self._atspi_readers[key] = LinuxAccessibilityReader(self, window)
            return self._atspi_readers[key].poll()
        talker = self._resolve_talker(window)
        if not talker:
            raise RuntimeError(f"[{window.title}] 未匹配 chatlog talker")
        with self._seen_lock:
            cursor = self._last_seq.get(talker)
            seen = self._seen_messages.get(talker, set())
        try:
            if cursor is None:
                response = self._chatlog.get("/api/chatlog/messages", params={
                    "group": talker, "limit": config.LINUX_CHATLOG_MESSAGE_LIMIT, "desc": True,
                })
                response.raise_for_status()
                rows = list(reversed(response.json()))
            else:
                rows = []
                limit = max(2, config.LINUX_CHATLOG_MESSAGE_LIMIT)
                offset = 0
                while True:
                    response = self._chatlog.post("/api/chatlog/v2/messages/batch", json={
                        "items": [{"group": talker, "since_seq": cursor}],
                        "limit": limit, "offset": offset,
                    })
                    response.raise_for_status()
                    page = response.json()["results"][talker]
                    rows.extend(page)
                    if len(page) < limit or any(row["seq"] > cursor for row in page):
                        break
                    offset += len(page)
            messages = [_ChatlogMessage.model_validate(row) for row in rows]
        except (httpx.HTTPError, ValidationError, KeyError) as exc:
            raise RuntimeError(f"[{window.title}] Linux chatlog 消息读取失败: {exc}") from exc

        new_texts = []
        identities = set()
        new_cursor = max(cursor or 0, max((message.seq for message in messages), default=0))
        for message in messages:
            identity = (message.seq, message.server_id, message.local_id)
            identities.add(identity)
            if identity in seen or message.is_self or (cursor is not None and message.seq < cursor):
                continue
            text = self._message_text(message)
            if text:
                new_texts.append(ReceivedMessage(
                    content=text, sender=message.sender, sender_name=message.sender_name,
                    at_users=message.at_users,
                ))
        with self._seen_lock:
            self._last_seq[talker] = new_cursor
            self._seen_messages[talker] = {identity for identity in seen | identities if identity[0] >= new_cursor}
        return new_texts

    def _resolve_talker(self, window: LinuxWeChatWindow) -> str | None:
        if window.talker:
            return window.talker
        # cache 空时 (启动早期 chatlog 尚未就绪) 复位退避锁, 每次都尝试刷新;
        # 已有 cache 但当前 title 不在里面, 走默认 TTL/retry backoff 避免狂刷
        if not self._title_to_talker:
            self._group_cache_retry_at = 0.0
        self._refresh_group_cache(force=True)
        window.talker = self._title_to_talker.get(window.title)
        return window.talker

    def _message_text(self, message: _ChatlogMessage) -> str:
        if message.msg_type == 1:
            return message.content
        if message.msg_type == 49:
            return self._appmsg_title(message.content)
        return ""

    @staticmethod
    def _appmsg_title(content: str) -> str:
        try:
            root = ET.fromstring(content.strip())
        except ET.ParseError:
            return ""
        title = root.findtext(".//title")
        return title.strip() if title else ""

    def send_text_to_window(self, window: LinuxWeChatWindow, text: str, *, mention_all: bool = False) -> bool:
        with self.ui_operation_lock:
            clipboard_owner: subprocess.Popen | bool | None = None
            if not self._activate_window(window):
                return False
            if not self._focus_input_area(window):
                return False
            if not self._send_context_matches(window) or not self._keys("ctrl+a", "Delete"):
                return False
            sent = False
            token = None
            try:
                sent_text = text
                if mention_all:
                    selected_name = self._select_mention(window, None)
                    sent_text = f"@{selected_name} {text}"
                remaining = text if mention_all else self._prepare_mentions(window, text)
                if remaining:
                    clipboard_owner = self._set_clipboard_text(remaining)
                    if not clipboard_owner:
                        return False
                token = self._sent_messages.record(window.expected_chat or window.title, sent_text)
                sent = self._paste_and_send(window, clear=False, paste=bool(remaining))
                if sent:
                    time.sleep(0.3)
                return sent
            finally:
                if token is not None and not sent:
                    self._sent_messages.discard(token)
                self._stop_clipboard_owner(clipboard_owner)
                if mention_all and not sent and self._focus_input_area(window) and self._send_context_matches(window):
                    self._keys("ctrl+a", "Delete")

    def send_image_to_window(self, window: LinuxWeChatWindow, image_base64: str) -> bool:
        image_data = base64.b64decode(image_base64)
        suffix = self._guess_image_suffix(image_data)
        temp_path = self._write_temp_file(image_data, suffix=suffix, filename=None)
        sent = False
        try:
            sent = self._send_file_path(window, temp_path, as_image=True)
            return sent
        finally:
            if not sent:
                self._schedule_temp_cleanup(temp_path)

    def send_file_to_window(self, window: LinuxWeChatWindow, file_base64: str, filename: str) -> bool:
        file_data = base64.b64decode(file_base64)
        temp_path = self._write_temp_file(file_data, suffix="", filename=filename)
        sent = False
        try:
            sent = self._send_file_path(window, temp_path)
            return sent
        finally:
            if not sent:
                self._schedule_temp_cleanup(temp_path)

    def send_file_path_to_window(self, window: LinuxWeChatWindow, file_path: Path, filename: str) -> bool:
        return self._send_file_path(window, str(file_path), cleanup=False)

    def send_voice_to_window(self, window, file_base64, filename, timeout=None) -> bool:
        if config.LINUX_MESSAGE_READER == "chatlog" and window.expected_chat is None:
            logger.error("Native Linux voice requires accessibility mode")
            return False
        from gateway.utils.linux_voice import connect_wechat_microphone, decoded_voice, microphone, play_voice

        if not self.SUPPORTS_VOICE:
            return False
        deadline = time.monotonic() + (timeout or config.WINDOWS_VOICE_TIMEOUT)
        try:
            with decoded_voice(file_base64) as (path, duration), self.ui_operation_lock:
                if deadline - time.monotonic() < max(4.5, duration) + 3:
                    raise TimeoutError("语音发送剩余时间不足")
                if not self._activate_window(window) or not self._focus_input_area(window) or not self._send_context_matches(window):
                    return False
                if not self._keys("ctrl+a", "Delete"):
                    return False
                cleaned = False
                with microphone(retain_modules=lambda: cleaned or self._recording_marker_path().exists()) as (sink, source, previous):
                    sent = False
                    self._mark_recording(window, source, previous)
                    try:
                        if not self._voice_button(window, {"Send Voice", "发送语音"}):
                            raise RuntimeError("启动微信录音失败")
                        started = time.monotonic()
                        while time.monotonic() - started < 2:
                            if connect_wechat_microphone(source):
                                break
                            time.sleep(.1)
                        else:
                            raise RuntimeError("微信未打开录音流")
                        play_voice(path, sink, deadline=deadline, context_matches=lambda: self._voice_context_matches(window))
                        time.sleep(max(.3, 4.5 - (time.monotonic() - started)))
                        if not self._voice_button(window, {"Send voice message", "发送语音消息"}):
                            raise RuntimeError("语音发送前会话或焦点发生变化")
                        sent = True
                        return True
                    finally:
                        if not self._finish_recording(window, cancel=not sent, cleanup=True):
                            raise RuntimeError("微信录音尚未确认结束,暂停后续 UI 操作")
                        cleaned = True
        except Exception as exc:
            logger.error("[%s] Linux 语音发送失败: %s", window.title, exc)
            return False

    def _voice_button(self, window, names, *, require_active=True):
        if not require_active and not names <= {"Cancel", "取消"}:
            return False
        if require_active and not self._active_window_matches(window):
            return False
        nodes = self.window_nodes(window)
        if window.expected_chat and chat_title(nodes, window.title) != window.expected_chat:
            return False
        field = input_node(nodes)
        if field is None:
            return False
        buttons = [node for node in nodes if node["role"] == "push button" and node["name"] in names
                   and node["enabled"] and node["rect"]
                   and node["rect"]["top"] >= field["rect"]["bottom"]]
        if len(buttons) != 1:
            return False
        if not self._active_window_matches(window):
            if require_active:
                return False
            return self._run_x11(["xdotool", "key", "--window", window.window_id, "Escape"]).returncode == 0
        rect = buttons[0]["rect"]
        return self._run_x11([
            "xdotool", "mousemove", str(rect["left"] + rect["width"] // 2),
            str(rect["top"] + rect["height"] // 2), "click", "1",
        ]).returncode == 0

    def _voice_context_matches(self, window):
        if not self._active_window_matches(window):
            return False
        return not window.expected_chat or chat_title(self.window_nodes(window), window.title) == window.expected_chat

    def _activate_window(self, window: LinuxWeChatWindow) -> bool:
        result = self._run_x11(["xdotool", "windowactivate", window.window_id], timeout=5)
        if result.returncode != 0:
            logger.error("[%s] 激活窗口失败: %s", window.title, result.stderr.strip())
            return False
        time.sleep(0.2)
        return True

    def _focus_input_area(self, window: LinuxWeChatWindow) -> bool:
        try:
            if (window.expected_chat or window.title) in self._ambiguous_titles:
                return False
            if not self._active_window_matches(window):
                return False
            if config.LINUX_MESSAGE_READER == "chatlog" and window.expected_chat is None:
                if not self._standalone_window_matches(window):
                    return False
                geometry = self._window_geometry(window.window_id)
                if not geometry:
                    return False
                x, y, width, height = geometry
                if width <= 0 or height <= 45 or not self._active_window_matches(window):
                    return False
                return self._run_x11([
                    "xdotool", "mousemove", str(x + width // 2), str(y + height - 45), "click", "1",
                ]).returncode == 0 and self._send_context_matches(window)
            nodes = self.window_nodes(window)
            field = input_node(nodes)
            if field is None or not field["enabled"]:
                return False
            if chat_title(nodes, window.title) != (window.expected_chat or window.title):
                return False
            self.accessibility.focus(field["ref"])
            rect = field["rect"]
            result = self._run_x11([
                "xdotool", "mousemove", str(rect["left"] + rect["width"] // 2),
                str(rect["top"] + rect["height"] // 2), "click", "1",
            ])
            return result.returncode == 0 and self._send_context_matches(window)
        except Exception as exc:
            logger.warning("Linux input targeting failed: %s", exc)
            return False

    def _window_geometry(self, window_id: str) -> tuple[int, int, int, int] | None:
        result = self._run_x11(["xwininfo", "-id", window_id], timeout=5)
        if result.returncode != 0:
            return None
        values: dict[str, int] = {}
        for line in result.stdout.splitlines():
            line = line.strip()
            if line.startswith("Absolute upper-left X:"):
                values["x"] = int(line.rsplit(None, 1)[-1])
            elif line.startswith("Absolute upper-left Y:"):
                values["y"] = int(line.rsplit(None, 1)[-1])
            elif line.startswith("Width:"):
                values["width"] = int(line.rsplit(None, 1)[-1])
            elif line.startswith("Height:"):
                values["height"] = int(line.rsplit(None, 1)[-1])
        if {"x", "y", "width", "height"} <= values.keys():
            return values["x"], values["y"], values["width"], values["height"]
        return None

    def _set_clipboard_text(self, text: str) -> subprocess.Popen | bool | None:
        if shutil.which("xsel"):
            result = self._run_x11(
                ["xsel", "--clipboard", "--input"],
                input_text=text,
                timeout=5,
            )
            if result.returncode != 0:
                logger.error("xsel 设置文本剪贴板失败: %s", result.stderr.strip())
                return None
            owner = True
        else:
            owner = self._start_clipboard_owner(
                ["xclip", "-quiet", "-selection", "clipboard", "-t", "text/plain;charset=utf-8"],
                text.encode(),
            )
        result = self._run_x11(["xclip", "-selection", "clipboard", "-o", "-t", "UTF8_STRING"], timeout=5)
        if result.returncode != 0 or result.stdout != text:
            self._stop_clipboard_owner(owner)
            logger.warning("剪贴板读回与本条文本不一致,停止粘贴")
            return None
        return owner

    def _keys(self, *keys: str) -> bool:
        return self._run_x11(["xdotool", "key", "--clearmodifiers", *keys], timeout=5).returncode == 0

    def _mention_popups(self):
        return [node for node in self.accessibility.frames() if not node["name"] and node["rect"]
                and node["rect"]["width"] > 0 and node["rect"]["height"] > 0]

    def _select_mention(self, window, name):
        if config.LINUX_MESSAGE_READER == "chatlog" and window.expected_chat is None:
            raise RuntimeError("Native mentions require accessibility")
        if not self._send_context_matches(window):
            raise RuntimeError("输入 @ 前会话或焦点发生变化")
        field = input_node(self.window_nodes(window))
        before = self.accessibility.text(field["ref"]).count("\ufffc")
        baseline = {node["ref"] for node in self._mention_popups()}
        if self._run_x11(["xdotool", "type", "--clearmodifiers", "@"], timeout=5).returncode != 0:
            raise RuntimeError("输入 @ 失败")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            popups = [node for node in self._mention_popups() if node["ref"] not in baseline]
            if len(popups) > 1:
                raise RuntimeError("无法唯一定位 @ 弹层")
            if popups:
                popup = popups[0]["ref"]
                break
            time.sleep(.05)
        else:
            raise RuntimeError("未打开 @ 弹层")
        owner = self._set_clipboard_text(name) if name is not None else None
        if name is not None and not owner:
            raise RuntimeError("设置 @ 名称失败")
        try:
            if name is not None and not self._keys("ctrl+v"):
                raise RuntimeError("粘贴 @ 名称失败")
            deadline = time.monotonic() + 3
            while time.monotonic() < deadline:
                nodes = self.accessibility.nodes(popup, max_nodes=200, max_depth=12)
                items = [node for node in nodes if node["role"] == "list item"]
                if name is None:
                    if items and items[0]["name"].strip().casefold() not in {"所有人", "mention all"}:
                        raise RuntimeError("This chat has no @all option; group owner or admin permissions are required")
                    matches = items[:1]
                else:
                    matches = [node for node in items if node["name"] == name]
                if len(matches) > 1:
                    raise RuntimeError("@ 候选存在重名")
                if matches:
                    fresh = self.accessibility.info(matches[0]["ref"])
                    rect = fresh["rect"]
                    if (fresh["name"] != matches[0]["name"] or not fresh["enabled"] or not rect or rect["width"] <= 0 or rect["height"] <= 0
                            or not self._active_window_matches(window)):
                        raise RuntimeError("@ 候选或焦点已变化")
                    result = self._run_x11([
                        "xdotool", "mousemove", str(rect["left"] + rect["width"] // 2),
                        str(rect["top"] + rect["height"] // 2), "mousedown", "1", "sleep", ".08", "mouseup", "1",
                    ])
                    if result.returncode != 0:
                        raise RuntimeError("选择 @ 候选失败")
                    break
                time.sleep(.05)
            else:
                raise RuntimeError("No @all candidate was found" if name is None else "未找到 @ 候选")
        finally:
            self._stop_clipboard_owner(owner)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if popup not in {node["ref"] for node in self._mention_popups()} and self._send_context_matches(window):
                field = input_node(self.window_nodes(window))
                if field and self.accessibility.text(field["ref"]).count("\ufffc") == before + 1:
                    return fresh["name"]
            time.sleep(.05)
        raise RuntimeError("未能确认原生 @ 已插入")

    def _prepare_mentions(self, window: LinuxWeChatWindow, text: str) -> str:
        if config.LINUX_MESSAGE_READER == "chatlog" and window.expected_chat is None:
            return text
        match = _AT_PREFIX_RE.match(text)
        if not match:
            return text
        names = [token[1:] for token in match[1].split()]
        if any(name.casefold() in {"所有人", "all"} for name in names):
            return text
        try:
            for name in names:
                self._select_mention(window, name)
            return match[2]
        except Exception as exc:
            logger.warning("原生 @ 失败,回退为原文: %s", exc)
            if not self._focus_input_area(window) or not self._send_context_matches(window) or not self._keys("ctrl+a", "Delete"):
                raise RuntimeError("无法安全清理 @ 草稿") from exc
            return text

    def _send_file_path(self, window: LinuxWeChatWindow, temp_path: str, *, as_image=False, cleanup=True) -> bool:
        with self.ui_operation_lock:
            clipboard_owner: subprocess.Popen | bool | None = None
            try:
                if not self._activate_window(window):
                    return False
                if not self._focus_input_area(window):
                    return False
                if as_image:
                    image = Path(temp_path).read_bytes()
                    suffix = self._guess_image_suffix(image).lstrip(".")
                    mime = "image/jpeg" if suffix == "jpg" else f"image/{suffix}"
                    clipboard_owner = self._start_clipboard_owner([
                        "xclip", "-quiet", "-selection", "clipboard", "-t", mime,
                    ], image)
                    target = self._run_x11(["xclip", "-selection", "clipboard", "-o", "-t", "TARGETS"])
                    if target.returncode != 0 or mime not in target.stdout.splitlines():
                        return False
                else:
                    clipboard_owner = self._set_clipboard_file(temp_path)
                    target = self._run_x11(["xclip", "-selection", "clipboard", "-o", "-t", "text/uri-list"])
                    if target.returncode != 0 or target.stdout.strip() != Path(temp_path).resolve().as_uri():
                        return False
                if not clipboard_owner:
                    return False
                token = None
                if not as_image:
                    token = self._sent_messages.record(window.expected_chat or window.title, os.path.basename(temp_path))
                if not self._paste_and_send(window, delay=2.0):
                    if token is not None:
                        self._sent_messages.discard(token)
                    return False
                if cleanup:
                    self._schedule_temp_cleanup(temp_path, delay=15 if as_image else 180)
                return True
            except Exception as exc:
                logger.error("[%s] 发送文件失败: %s", window.title, exc)
                return False
            finally:
                self._stop_clipboard_owner(clipboard_owner)

    def _set_clipboard_file(self, path: str) -> subprocess.Popen | None:
        uri = Path(path).resolve().as_uri()
        return self._start_clipboard_owner(
            ["xclip", "-quiet", "-selection", "clipboard", "-t", "text/uri-list"],
            (uri + "\n").encode(),
        )

    def _active_window_matches(self, window: LinuxWeChatWindow) -> bool:
        active = self._run_x11(["xdotool", "getactivewindow"], timeout=3)
        try:
            matches = active.returncode == 0 and int(active.stdout.strip(), 0) == int(window.window_id, 16)
        except ValueError:
            return False
        if not matches:
            return False
        title = self._run_x11(["xdotool", "getwindowname", window.window_id], timeout=3)
        return title.returncode == 0 and title.stdout.strip() == window.title

    def _send_context_matches(self, window: LinuxWeChatWindow) -> bool:
        if (window.expected_chat or window.title) in self._ambiguous_titles:
            return False
        if not self._active_window_matches(window):
            return False
        if config.LINUX_MESSAGE_READER == "chatlog" and window.expected_chat is None:
            return self._standalone_window_matches(window)
        try:
            nodes = self.window_nodes(window)
            field = input_node(nodes)
            return (field is not None and field["enabled"] and field["focused"]
                    and chat_title(nodes, window.title) == (window.expected_chat or window.title))
        except Exception as exc:
            logger.warning("AT-SPI 发送上下文检查失败: %s", exc)
            return False

    def _standalone_window_matches(self, window: LinuxWeChatWindow) -> bool:
        if window.expected_chat is not None or window.title in _MAIN_WINDOW_TITLES:
            return False
        matches = [candidate for candidate in self.list_windows() if candidate.title == window.title]
        return len(matches) == 1 and matches[0].window_id == window.window_id

    def _paste_and_send(self, window: LinuxWeChatWindow, *, delay=.35, clear=True, paste=True) -> bool:
        if not self._send_context_matches(window):
            return False
        if clear and not self._keys("ctrl+a", "Delete"):
            return False
        if paste and not self._keys("ctrl+v"):
            logger.error("[%s] xdotool 粘贴失败", window.title)
            return False
        time.sleep(delay)
        if not self._send_context_matches(window):
            logger.warning("[%s] 回车前焦点或当前会话发生变化,停止发送", window.title)
            return False
        send = self._run_x11(["xdotool", "key", "--clearmodifiers", "Return"], timeout=5)
        if send.returncode != 0:
            logger.error("[%s] xdotool 回车发送失败: %s", window.title, send.stderr.strip())
            return False
        return True

    def _start_clipboard_owner(self, args: list[str], payload: bytes) -> subprocess.Popen | None:
        try:
            process = subprocess.Popen(
                args,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                env=_x11_env(),
            )
            if process.stdin is None:
                process.kill()
                return None
            process.stdin.write(payload)
            process.stdin.close()
            time.sleep(0.1)
            if process.poll() is not None and process.returncode != 0:
                stderr = process.stderr.read().decode(errors="replace").strip() if process.stderr else ""
                logger.error("xclip 设置剪贴板失败: %s", stderr)
                return None
            return process
        except (OSError, BrokenPipeError) as exc:
            logger.error("xclip 启动失败: %s", exc)
            return None

    @staticmethod
    def _stop_clipboard_owner(process: subprocess.Popen | bool | None) -> None:
        if not process or process is True:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
        with contextlib.suppress(Exception):
            if process.stderr:
                process.stderr.close()

    @staticmethod
    def _guess_image_suffix(image_data: bytes) -> str:
        if image_data[:2] == b"\xff\xd8":
            return ".jpg"
        if image_data[:4] == b"GIF8":
            return ".gif"
        if image_data[:4] == b"RIFF" and image_data[8:12] == b"WEBP":
            return ".webp"
        return ".png"

    def _write_temp_file(self, data: bytes, *, suffix: str, filename: str | None) -> str:
        temp_dir = None
        fd = None
        if filename:
            name = self._safe_filename(filename)
            temp_dir = tempfile.mkdtemp(prefix="awsl-wechat-")
            path = os.path.join(temp_dir, name)
        else:
            fd, path = tempfile.mkstemp(prefix="awsl-wechat-", suffix=suffix)
        try:
            with (os.fdopen(fd, "wb") if fd is not None else open(path, "wb")) as file:
                file.write(data)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(path)
            if temp_dir:
                with contextlib.suppress(OSError):
                    os.rmdir(temp_dir)
            raise
        return path

    @staticmethod
    def _safe_filename(filename: str) -> str:
        name = os.path.basename(filename).strip().replace("\0", "_")
        if name in {"", ".", ".."}:
            return "attachment.bin"
        if len(name.encode("utf-8")) <= 200:
            return name
        stem, extension = os.path.splitext(name)
        tag = "-" + hashlib.sha256(name.encode("utf-8")).hexdigest()[:8]
        budget = 200 - len((tag + extension).encode("utf-8"))
        if budget <= 0:
            stem, extension, budget = name, "", 200 - len(tag)
        return stem.encode("utf-8")[:budget].decode("utf-8", errors="ignore") + tag + extension

    def _schedule_temp_cleanup(self, temp_path: str, *, delay: float | None = None) -> None:
        threading.Thread(
            target=self._cleanup_temp_path,
            args=(temp_path, delay),
            daemon=True,
            name="linux-wechat-temp-cleanup",
        ).start()

    def _cleanup_temp_path(self, temp_path: str, delay: float | None = None) -> None:
        time.sleep(self._TEMP_CLEANUP_DELAY if delay is None else delay)
        with contextlib.suppress(Exception):
            os.unlink(temp_path)
        temp_dir = os.path.dirname(temp_path)
        if os.path.basename(temp_dir).startswith("awsl-wechat-"):
            with contextlib.suppress(Exception):
                os.rmdir(temp_dir)


class LinuxAccessibilityReader:
    def __init__(self, adapter, window):
        self.adapter = adapter
        self.window = window
        from gateway.console.adapters.linux import LinuxConsoleAdapter

        self.parser = LinuxConsoleAdapter(adapter)
        self._previous = None

    def poll(self):
        nodes = self.adapter.window_nodes(self.window)
        if chat_title(nodes, self.window.title) != (self.window.expected_chat or self.window.title):
            raise RuntimeError("辅助功能读取的会话发生变化")
        parent = self.parser._list_node(nodes, {"消息", "Messages"})
        if parent is None:
            raise RuntimeError("AT-SPI 未找到消息列表,保留原基线")
        rows = self.parser._children(nodes, parent)
        parsed = self.parser._message_items(nodes, parent)
        current = [(row["ref"], row["name"]) for row in rows]
        previous = self._previous
        if not current:
            if previous is None:
                self._previous = []
            return []
        self._previous = current
        if previous is None:
            return []
        # 仅接收旧列表尾部之后追加的行; 失去重叠时重建基线,避免滚动历史造成重播。
        boundary = next((i + 1 for i in range(len(current) - 1, -1, -1) if current[i] == previous[-1]), None) if previous else 0
        if boundary is None:
            return []
        result = []
        for item in parsed[boundary:]:
            text = item["raw_text"].strip()
            if (item["kind"] == "timestamp" or not text or _VOICE_MESSAGE_RE.fullmatch(text)
                    or text in {"Animated Stickers", "[图片]", "[表情]", "[视频]", "[语音]"}):
                continue
            if self.adapter._sent_messages.is_self_sent(self.window.expected_chat or self.window.title, item["content"]):
                continue
            result.append(ReceivedMessage(content=text))
        return result


class LinuxSentMessages:
    def __init__(self):
        self._records = deque(maxlen=200)
        self._lock = threading.Lock()

    @staticmethod
    def _normalize(text):
        return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()

    def record(self, key, content):
        token = object()
        with self._lock:
            self._records.append((token, time.monotonic(), key, self._normalize(content)))
        return token

    def discard(self, token):
        with self._lock:
            self._records = deque((item for item in self._records if item[0] is not token), maxlen=200)

    def is_self_sent(self, key, content):
        text = self._normalize(content)
        if not text:
            return False
        now = time.monotonic()
        with self._lock:
            for record in self._records:
                _, stamp, name, value = record
                if now - stamp <= 120 and name == key and value == text:
                    self._records.remove(record)
                    return True
        return False
