"""Linux 微信适配器.

接收消息通过 chatlog HTTP API 读取,发送通过 X11 窗口激活和剪贴板完成.
"""

from __future__ import annotations

import base64
import contextlib
import logging
import os
import shutil
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

import httpx
from pydantic import ValidationError

from common.config import config
from common.models import ReceivedMessage
from gateway.adapters.base import BaseWeChatAdapter, DiscoveredWindow

logger = logging.getLogger(__name__)

_MAIN_WINDOW_TITLES = {"微信", "WeChat", "Weixin"}
_WECHAT_CLASS = "wechat.wechat"


class _ChatlogMessage(ReceivedMessage):
    time: str
    msg_type: int
    is_self: bool


@dataclass
class LinuxWeChatWindow:
    title: str
    window_id: str
    talker: str | None = None

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
        return result.returncode == 0


def _x11_env() -> dict[str, str]:
    env = os.environ.copy()
    if not env.get("DISPLAY"):
        env["DISPLAY"] = config.LINUX_WECHAT_DISPLAY
    if not env.get("XAUTHORITY") and config.LINUX_WECHAT_XAUTHORITY:
        env["XAUTHORITY"] = os.path.expanduser(config.LINUX_WECHAT_XAUTHORITY)
    return env


class LinuxWeChatAdapter(BaseWeChatAdapter):
    _TEMP_CLEANUP_DELAY: ClassVar[float] = 15.0
    _CHATLOG_RETRY_INTERVAL: ClassVar[float] = 5.0

    def __init__(self) -> None:
        self._send_lock = threading.Lock()
        self._chatlog = httpx.Client(
            base_url=self._chatlog_base_url(),
            headers={"Authorization": f"Bearer {config.HTTP_API_TOKEN}"} if config.HTTP_API_TOKEN else {},
            timeout=httpx.Timeout(connect=5.0, read=30.0, write=10.0, pool=5.0),
        )
        self._group_cache_at = 0.0
        self._group_cache_retry_at = 0.0
        self._title_to_talker: dict[str, str] = {}
        self._window_title_to_talker: dict[str, str] = {}
        self._last_pull: dict[str, str] = {}
        self._seen_lock = threading.Lock()
        self._ensure_tools()

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

        title_to_talker: dict[str, str] = {}
        for group in groups:
            username = group.get("username", "")
            names = {
                username,
                group.get("display_name", ""),
                group.get("remark", ""),
                group.get("nick_name", ""),
            }
            for name in names:
                if name and username:
                    title_to_talker[name] = username

        self._title_to_talker = title_to_talker
        self._group_cache_at = now
        self._group_cache_retry_at = 0.0

    def find_all_wechat_windows(self) -> list[DiscoveredWindow]:
        result = self._run_x11(["wmctrl", "-lx"], timeout=5)
        if result.returncode != 0:
            logger.warning("wmctrl 枚举窗口失败: %s", result.stderr.strip())
            return []

        self._refresh_group_cache()
        windows: list[DiscoveredWindow] = []
        for line in result.stdout.splitlines():
            parts = line.split(None, 4)
            if len(parts) < 5:
                continue
            window_id, _desktop, window_class, _host, title = parts
            if window_class != _WECHAT_CLASS or title in _MAIN_WINDOW_TITLES:
                continue
            if not title:
                continue
            talker = self._window_title_to_talker.get(title) or self._title_to_talker.get(title)
            if talker:
                self._window_title_to_talker[title] = talker
            window = LinuxWeChatWindow(title=title, window_id=window_id, talker=talker)
            windows.append(
                DiscoveredWindow(
                    title=title,
                    window=window,
                    debug_info=None,
                )
            )
            logger.debug("发现 Linux 微信窗口: %s (%s, talker=%s)", title, window_id, talker)

        return windows

    def get_messages_from_window(self, window: LinuxWeChatWindow) -> list[ReceivedMessage]:
        talker = self._resolve_talker(window)
        if not talker:
            raise RuntimeError(f"[{window.title}] 未匹配 chatlog talker")

        with self._seen_lock:
            last_time = self._last_pull.get(talker, "")

        params: dict = {
            "group": talker,
            "limit": config.LINUX_CHATLOG_MESSAGE_LIMIT,
            "desc": True,
        }
        # 增量: chatlog start 是 'YYYY-MM-DD HH:MM:SS', 用上次最大 time 做下界 (含等号,
        # 同秒边界靠下面 strict-greater 过滤)
        if last_time:
            params["start"] = last_time.replace("T", " ")[:19]

        try:
            response = self._chatlog.get("/api/chatlog/messages", params=params)
            response.raise_for_status()
            messages = [_ChatlogMessage.model_validate(message) for message in reversed(response.json())]
        except (httpx.HTTPError, ValidationError) as exc:
            raise RuntimeError(f"[{window.title}] Linux chatlog 消息读取失败: {exc}") from exc

        new_texts: list[ReceivedMessage] = []
        new_last_time = last_time
        for message in messages:
            msg_time = message.time
            # strict-greater 过滤同秒边界 (chatlog start 是 >=)
            if last_time and msg_time <= last_time:
                continue
            # 跳过自己发的消息. 不推进 new_last_time, 防止把同秒别人的消息一并丢掉
            # (chatlog start 是 >= 边界 + 下面 strict-greater 过滤是 <=, 同秒边界
            # 必须等下一轮通过原 last_time 把别人的消息也带出来).
            if message.is_self:
                continue
            text = self._message_text(message)
            if not text:
                continue
            new_texts.append(ReceivedMessage(
                content=text, sender=message.sender, sender_name=message.sender_name,
                at_users=message.at_users,
            ))
            if msg_time > new_last_time:
                new_last_time = msg_time

        if not new_texts:
            return []

        with self._seen_lock:
            self._last_pull[talker] = new_last_time
        return new_texts

    def _resolve_talker(self, window: LinuxWeChatWindow) -> str | None:
        if window.talker:
            return window.talker
        talker = self._window_title_to_talker.get(window.title)
        if talker:
            window.talker = talker
            return talker
        # cache 空时 (启动早期 chatlog 尚未就绪) 复位退避锁, 每次都尝试刷新;
        # 已有 cache 但当前 title 不在里面, 走默认 TTL/retry backoff 避免狂刷
        if not self._title_to_talker:
            self._group_cache_retry_at = 0.0
        self._refresh_group_cache(force=True)
        window.talker = self._title_to_talker.get(window.title)
        if window.talker:
            self._window_title_to_talker[window.title] = window.talker
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

    def send_text_to_window(self, window: LinuxWeChatWindow, text: str, *, reply_to_message: str | None = None) -> bool:
        if reply_to_message:
            return False
        with self._send_lock:
            clipboard_owner: subprocess.Popen | bool | None = None
            if not self._activate_window(window):
                return False
            if not self._focus_input_area(window):
                return False
            clipboard_owner = self._set_clipboard_text(text)
            if not clipboard_owner:
                return False
            try:
                if not self._paste_and_send(window):
                    return False
                time.sleep(0.3)
                return True
            finally:
                self._stop_clipboard_owner(clipboard_owner)

    def send_image_to_window(self, window: LinuxWeChatWindow, image_base64: str) -> bool:
        image_data = base64.b64decode(image_base64)
        suffix = self._guess_image_suffix(image_data)
        temp_path = self._write_temp_file(image_data, suffix=suffix, filename=None)
        sent = self._send_file_path(window, temp_path)
        if not sent:
            self._schedule_temp_cleanup(temp_path)
        return sent

    def send_file_to_window(self, window: LinuxWeChatWindow, file_base64: str, filename: str) -> bool:
        file_data = base64.b64decode(file_base64)
        temp_path = self._write_temp_file(file_data, suffix="", filename=filename)
        sent = self._send_file_path(window, temp_path)
        if not sent:
            self._schedule_temp_cleanup(temp_path)
        return sent

    def _activate_window(self, window: LinuxWeChatWindow) -> bool:
        result = self._run_x11(["xdotool", "windowactivate", window.window_id], timeout=5)
        if result.returncode != 0:
            logger.error("[%s] 激活窗口失败: %s", window.title, result.stderr.strip())
            return False
        time.sleep(0.2)
        return True

    def _focus_input_area(self, window: LinuxWeChatWindow) -> bool:
        geometry = self._window_geometry(window.window_id)
        if not geometry:
            return True
        x, y, width, height = geometry
        click_x = x + max(20, width // 2)
        click_y = y + max(20, height - 45)
        result = self._run_x11(["xdotool", "mousemove", str(click_x), str(click_y), "click", "1"], timeout=5)
        if result.returncode != 0:
            logger.debug("[%s] 点击输入区域失败: %s", window.title, result.stderr.strip())
            return False
        time.sleep(0.1)
        return True

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
            return True

        return self._start_clipboard_owner(
            ["xclip", "-quiet", "-selection", "clipboard", "-t", "text/plain;charset=utf-8"],
            text.encode(),
        )

    def _send_file_path(self, window: LinuxWeChatWindow, temp_path: str) -> bool:
        with self._send_lock:
            clipboard_owner: subprocess.Popen | bool | None = None
            try:
                if not self._activate_window(window):
                    return False
                if not self._focus_input_area(window):
                    return False
                clipboard_owner = self._set_clipboard_file(temp_path)
                if not clipboard_owner:
                    return False
                if not self._paste_and_send(window):
                    return False
                self._schedule_temp_cleanup(temp_path)
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

    def _paste_and_send(self, window: LinuxWeChatWindow) -> bool:
        paste = self._run_x11(["xdotool", "key", "--clearmodifiers", "ctrl+v"], timeout=5)
        if paste.returncode != 0:
            logger.error("[%s] xdotool 粘贴失败: %s", window.title, paste.stderr.strip())
            return False
        time.sleep(0.2)
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
        if filename:
            temp_dir = tempfile.mkdtemp(prefix="awsl-wechat-")
            path = os.path.join(temp_dir, self._safe_filename(filename))
            with open(path, "wb") as file:
                file.write(data)
            return path
        fd, path = tempfile.mkstemp(prefix="awsl-wechat-", suffix=suffix)
        with os.fdopen(fd, "wb") as file:
            file.write(data)
        return path

    @staticmethod
    def _safe_filename(filename: str) -> str:
        name = os.path.basename(filename).strip()
        return name[:200] or "attachment.bin"

    def _schedule_temp_cleanup(self, temp_path: str) -> None:
        threading.Thread(
            target=self._cleanup_temp_path,
            args=(temp_path,),
            daemon=True,
            name="linux-wechat-temp-cleanup",
        ).start()

    def _cleanup_temp_path(self, temp_path: str) -> None:
        time.sleep(self._TEMP_CLEANUP_DELAY)
        with contextlib.suppress(Exception):
            os.unlink(temp_path)
        temp_dir = os.path.dirname(temp_path)
        if os.path.basename(temp_dir).startswith("awsl-wechat-"):
            with contextlib.suppress(Exception):
                os.rmdir(temp_dir)
