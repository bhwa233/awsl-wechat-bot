import contextlib
import hashlib
import logging
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import ClassVar

import httpx
from pydantic import computed_field

logging.getLogger("comtypes").setLevel(logging.WARNING)

import uiautomation as auto  # noqa: E402

from common.config import config  # noqa: E402
from common.models import ReceivedMessage  # noqa: E402
from gateway.adapters.base import BaseWeChatAdapter, DiscoveredWindow  # noqa: E402
from gateway.metrics import metrics  # noqa: E402
from gateway.models import GroupDebugInfo  # noqa: E402

logger = logging.getLogger(__name__)

_WS_RE = re.compile(r'\s+')
_QUOTE_RE = re.compile(r"引用\s+(.+?)\s+的消息\s*[:：]\s*(.+)$", re.S)
_SIMILARITY_THRESHOLD = 0.75
_SIMILARITY_MIN_LEN = 6


def _normalize_message(text: str) -> str:
    return _WS_RE.sub(' ', text.strip())


def _is_similar_message(a: str, b: str) -> bool:
    if not a or not b:
        return False
    ratio = len(a) / len(b) if len(b) > 0 else 0
    if ratio < 0.5 or ratio > 2.0:
        return False
    return SequenceMatcher(None, a, b).ratio() >= _SIMILARITY_THRESHOLD


class _SentMessageStore:
    def __init__(self, ttl: float = 120.0, maxlen: int = 200):
        self._lock = threading.Lock()
        self._records: deque[tuple[object, float, str, str, str]] = deque(maxlen=maxlen)
        self._ttl = ttl

    def record(self, conversation_key: str, content: str) -> object:
        with self._lock:
            now = time.time()
            while self._records and now - self._records[0][1] > self._ttl:
                self._records.popleft()
            token = object()
            self._records.append((token, now, conversation_key, content, _normalize_message(content)))
            return token

    def discard(self, token: object) -> None:
        with self._lock:
            for record in self._records:
                if record[0] is token:
                    self._records.remove(record)
                    return

    def is_self_sent(self, conversation_key: str, content: str) -> bool:
        now = time.time()
        norm = _normalize_message(content)
        if not norm:
            return False
        with self._lock:
            for _, ts, stored_key, stored_content, stored_norm in reversed(self._records):
                if now - ts > self._ttl or stored_key != conversation_key:
                    continue
                if stored_content == content or stored_norm == norm:
                    return True
                if len(stored_norm) >= 8 and len(norm) >= 8 and (
                    stored_norm.startswith(norm) or norm.startswith(stored_norm)
                ):
                    return True
                if (
                    len(stored_norm) >= _SIMILARITY_MIN_LEN
                    and len(norm) >= _SIMILARITY_MIN_LEN
                    and (stored_norm in norm or norm in stored_norm)
                ):
                    return True
                if (
                    len(stored_norm) >= _SIMILARITY_MIN_LEN
                    and len(norm) >= _SIMILARITY_MIN_LEN
                    and _is_similar_message(stored_norm, norm)
                ):
                    return True
        return False


@dataclass(frozen=True, slots=True)
class _MessageRecord:
    message: ReceivedMessage | None
    runtime_id: tuple[int, ...] | None
    fingerprint: str


@dataclass(slots=True)
class _MessageState:
    observed: list[_MessageRecord] = field(default_factory=list)
    previous: list[_MessageRecord] | None = None
    seen_ids: dict[tuple[int, ...], str] = field(default_factory=dict)


class WindowsGroupDebugInfo(GroupDebugInfo):
    window_id: int | None = None
    automation_id: str | None = None

    @computed_field
    @property
    def chat_id(self) -> str | None:
        return WindowsWeChatAdapter.standalone_conversation_id(self.automation_id)


class WindowsWeChatAdapter(BaseWeChatAdapter):
    SUPPORTS_VOICE = True
    STANDALONE_AUTOMATION_PREFIX: ClassVar[str] = "ChatSingleWindow"
    _SEEN_RUNTIME_IDS_LIMIT = 256
    _ILLEGAL_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
    _RESERVED_NAMES: ClassVar[set[str]] = {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
    _DROPFILE_CLEANUP_DELAY = 60.0
    _DROPFILE_CLEANUP_RETRY_DELAY = 1.0
    _DROPFILE_CLEANUP_RETRIES = 10
    _VOICE_ALT_KEY = auto.Keys.VK_RMENU
    _VOICE_RECORDING_START_DELAY = 0.4
    _VOICE_RECORDING_FINISH_DELAY = 0.3
    _VOICE_MIN_RECORDING_DURATION = 4.5

    @classmethod
    def standalone_conversation_id(cls, automation_id: str | None) -> str | None:
        if not isinstance(automation_id, str):
            return None
        if not automation_id.startswith(cls.STANDALONE_AUTOMATION_PREFIX):
            return None
        conversation_id = automation_id[len(cls.STANDALONE_AUTOMATION_PREFIX):]
        return conversation_id or None

    @classmethod
    def _sanitize_windows_filename(cls, filename: str | None, default: str = "attachment.bin") -> str:
        name = os.path.basename((filename or "").strip())
        name = cls._ILLEGAL_FILENAME_CHARS.sub("_", name)
        name = name.rstrip(" .")
        if not name:
            return default
        stem, ext = os.path.splitext(name)
        if stem.upper() in cls._RESERVED_NAMES:
            stem = "_" + stem
            name = stem + ext
        return name[:200]

    def __init__(self):
        self._db_reader = WindowsChatlogReader(self) if config.WINDOWS_MESSAGE_READER == "chatlog" else None
        self._send_lock = threading.Lock()  # 仅发送需要锁(激活窗口+剪贴板+按键)
        self._sent_messages = _SentMessageStore()
        self._thread_inits: dict[int, auto.UIAutomationInitializerInThread] = {}
        self._message_states: dict[str, _MessageState] = {}
        self._message_state_lock = threading.Lock()

    def _is_self_sent_message(self, state_key: str, content: str) -> bool:
        content = content.strip()
        match = _QUOTE_RE.search(content)
        if match and match.group(1).strip() and match.group(2).strip():
            content = content[:match.start()].strip()
        return self._sent_messages.is_self_sent(state_key, content)

    def select_new_messages(
        self,
        window,
        messages: list[ReceivedMessage],
        *,
        initialize: bool = False,
    ) -> list[ReceivedMessage]:
        key = self._message_state_key(window)
        if config.WINDOWS_MESSAGE_READER == "chatlog":
            return [] if initialize else messages
        with self._message_state_lock:
            state = self._message_states.setdefault(key, _MessageState())
            current = state.observed
            if initialize:
                self._set_message_baseline(state, current)
                return []
            if state.previous is None:
                self._set_message_baseline(state, current)
                return []
            if not current:
                return []

            new_start = self._find_new_message_start(state, current)
            self._set_message_baseline(state, current)
        if new_start is None:
            latest = next((record.message for record in reversed(current) if record.message), None)
            candidates = [latest] if latest else []
        else:
            candidates = [record.message for record in current[new_start:] if record.message is not None]

        new_messages = []
        for message in candidates:
            if self._is_self_sent_message(key, message.content):
                metrics.increment("detected_messages_total", group=window.Name, result="self_filtered")
                logger.debug(f"[{window.Name}] 跳过自己发送的消息: {message.content[:30]}...")
                continue
            new_messages.append(message)
        return new_messages

    def _find_new_message_start(
        self,
        state: _MessageState,
        current: list[_MessageRecord],
    ) -> int | None:
        previous = state.previous or []
        previous_ids = {
            record.runtime_id: (index, record.fingerprint)
            for index, record in enumerate(previous)
            if record.runtime_id is not None
        }
        direct_matches = []
        for current_index, record in enumerate(current):
            match = previous_ids.get(record.runtime_id)
            if match is not None and match[1] == record.fingerprint:
                direct_matches.append((current_index, match[0]))

        boundary = 0
        if direct_matches:
            current_index, previous_index = max(direct_matches)
            boundary = current_index + 1
            while boundary < len(current) and previous_index + 1 < len(previous):
                previous_index += 1
                if current[boundary].fingerprint != previous[previous_index].fingerprint:
                    break
                boundary += 1

        for index, record in enumerate(current):
            if record.runtime_id is not None and state.seen_ids.get(record.runtime_id) == record.fingerprint:
                boundary = max(boundary, index + 1)

        if not boundary:
            previous_hashes = [record.fingerprint for record in previous]
            current_hashes = [record.fingerprint for record in current]
            blocks = SequenceMatcher(
                None,
                previous_hashes,
                current_hashes,
                autojunk=False,
            ).get_matching_blocks()
            boundary = max((block.b + block.size for block in blocks if block.size), default=0)

        return boundary if boundary else None

    def _set_message_baseline(self, state: _MessageState, records: list[_MessageRecord]) -> None:
        state.previous = records
        for record in records:
            if record.runtime_id is not None:
                state.seen_ids[record.runtime_id] = record.fingerprint
        while len(state.seen_ids) > self._SEEN_RUNTIME_IDS_LIMIT:
            state.seen_ids.pop(next(iter(state.seen_ids)))

    @classmethod
    def _message_state_key(cls, window) -> str:
        automation_id = cls._automation_id(window)
        conversation_id = cls.standalone_conversation_id(automation_id)
        if conversation_id:
            return conversation_id
        window_id = cls._native_window_handle(window)
        return automation_id or f"window:{window_id or id(window)}"

    @staticmethod
    def _message_runtime_id(control) -> tuple[int, ...] | None:
        try:
            runtime_id = tuple(int(part) for part in control.GetRuntimeId())
        except Exception:
            return None
        return runtime_id or None

    @staticmethod
    def _message_fingerprint(text: str, class_name: str) -> str:
        return hashlib.sha256(f"{class_name}\0{text}".encode()).hexdigest()[:16]

    @staticmethod
    def _triggerable_message(text: str, class_name: str) -> str | None:
        if class_name == 'mmui::ChatVoiceItemView' or not text or len(text) < 2:
            return None
        if text in ['[图片]', '[表情]', '[视频]', '[文件]', 'Animated Stickers']:
            return None
        if re.match(r'^[\d:]+$', text):
            return None
        return text

    @property
    def ui_operation_lock(self) -> threading.Lock:
        """前台 UI 操作锁: 发送和 Console 操作必须共用同一把锁."""
        return self._send_lock

    def _ensure_uia(self):
        """确保当前线程已初始化 UIA(内部处理 COM 初始化,控件须在同一线程创建和使用)"""
        tid = threading.get_ident()
        if tid not in self._thread_inits:
            self._thread_inits[tid] = auto.UIAutomationInitializerInThread()
            logger.debug(f"线程 {tid} UIA 初始化完成")

    def _set_clipboard_text_windows_api(self, text: str):
        """使用 win32 API 安全设置文本剪贴板."""
        import win32clipboard
        import win32con

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            with contextlib.suppress(Exception):
                win32clipboard.CloseClipboard()

    def _set_clipboard_data(self, format_type: int, data):
        """使用 win32 API 安全设置剪贴板二进制数据."""
        import win32clipboard

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(format_type, data)
        finally:
            with contextlib.suppress(Exception):
                win32clipboard.CloseClipboard()

    def set_clipboard_text_verified(self, text: str) -> None:
        """设置文本剪贴板并校验,必要时回退到 Windows API."""
        auto.SetClipboardText(text)
        clipboard_content = auto.GetClipboardText()
        if clipboard_content == text:
            return

        if config.DEBUG:
            logger.debug(f"剪贴板截断!原始长度={len(text)} 剪贴板长度={len(clipboard_content)}")
            logger.debug(f"原始内容: {text!r}")
            logger.debug(f"剪贴板内容: {clipboard_content!r}")

        self._set_clipboard_text_windows_api(text)

        if config.DEBUG:
            logger.debug("已使用 win32clipboard 重新设置剪贴板")

    @staticmethod
    def _guess_image_filename(image_data: bytes) -> str:
        if image_data[:2] == b'\xff\xd8':
            return "image.jpg"
        if image_data[:4] == b'GIF8':
            return "image.gif"
        if image_data[:4] == b'RIFF' and image_data[8:12] == b'WEBP':
            return "image.webp"
        return "image.png"

    @classmethod
    def _safe_filename(cls, filename: str | None, default_name: str) -> str:
        safe_name = os.path.basename(filename or "").strip()
        if not safe_name:
            return default_name
        safe_name = cls._ILLEGAL_FILENAME_CHARS.sub("_", safe_name)
        safe_name = safe_name.rstrip(" .")
        if not safe_name:
            return default_name

        stem, ext = os.path.splitext(safe_name)
        if stem.upper() in cls._RESERVED_NAMES:
            safe_name = f"_{stem}{ext}"
        return (safe_name or default_name)[:200]

    def _write_temp_file(self, data: bytes, *, filename: str | None, default_name: str) -> tuple[str, str]:
        import tempfile

        temp_dir = tempfile.mkdtemp(prefix="awsl-wechat-")
        temp_path = os.path.join(temp_dir, self._safe_filename(filename, default_name))
        with open(temp_path, 'wb') as f:
            f.write(data)
        return temp_path, temp_dir

    def _paste_file_to_window(self, window, temp_path: str):
        import struct

        import win32clipboard

        temp_path_bytes = temp_path.encode('utf-16-le') + b'\x00\x00'
        dropfiles = struct.pack('IIIIi', 20, 0, 0, 0, 1) + temp_path_bytes + b'\x00\x00'
        self._set_clipboard_data(win32clipboard.CF_HDROP, dropfiles)

    def _find_chat_input_control(self, window):
        target = window.EditControl(searchDepth=25, AutomationId='chat_input_field')
        if target.Exists(0):
            return target
        edit_controls = []
        for control, _depth in auto.WalkControl(window, maxDepth=6):
            if control.ControlTypeName != 'EditControl':
                continue
            try:
                if not control.Exists(0):
                    continue
            except Exception:
                continue
            edit_controls.append(control)
        if not edit_controls:
            logger.debug(f"[{window.Name}] 未找到 EditControl,回退到窗口级按键")
            return None
        target = edit_controls[-1]
        logger.debug(f"[{window.Name}] 使用输入控件: Name={target.Name!r} AutomationId={target.AutomationId!r}")
        return target

    def _focus_send_target(self, window):
        target = self._find_chat_input_control(window)
        if target is None:
            return window
        try:
            target.Click(waitTime=0)
        except Exception as e:
            logger.debug(f"[{window.Name}] 点击输入框失败,继续尝试聚焦: {e}")
        try:
            target.SetFocus()
        except Exception as e:
            logger.debug(f"[{window.Name}] 输入框 SetFocus 失败,继续尝试按键发送: {e}")
        time.sleep(0.2)
        return target

    def _send_file_paste_and_enter(self, target, conversation_key: str, filename: str):
        time.sleep(0.3)
        target.SendKeys('{CTRL}v', waitTime=0.5)
        time.sleep(1.0)
        token = self._sent_messages.record(conversation_key, filename)
        try:
            target.SendKeys('{ENTER}', waitTime=0.5)
        except Exception:
            self._sent_messages.discard(token)
            raise

    _AT_PREFIX_RE = re.compile(r'^((?:@[^\s@,，;；:：]+\s+)+)(.*)', re.DOTALL)
    _AT_POPOVER_TIMEOUT = 0.5
    _AT_POPOVER_GONE_TIMEOUT = 0.3
    _AT_POPOVER_INTERVAL = 0.05

    @staticmethod
    def _parse_at_prefix(text: str) -> tuple[list[str], str]:
        """从消息开头解析 @ 名字列表，返回 (names, remaining_body)。

        匹配规则：开头若干个 ``@<昵称>+<空白>``，碰到分隔符或空白结束。
        """
        m = WindowsWeChatAdapter._AT_PREFIX_RE.match(text)
        if not m:
            return [], text
        prefix, body = m.group(1), m.group(2)
        names = [tok[1:] for tok in prefix.split() if tok.startswith('@')]
        return names, body

    def _count_at_popovers(self, window) -> int:
        """统计窗口子树里 mmui::XPopover 控件数量。"""
        try:
            return sum(
                1 for ctrl, _depth in auto.WalkControl(window, maxDepth=10)
                if ctrl.ClassName == 'mmui::XPopover' and ctrl.Exists(0)
            )
        except Exception as e:
            if config.DEBUG:
                logger.debug(f"_count_at_popovers 异常: {e}")
            return 0

    def _at_popover_has_candidate(self, window) -> bool:
        """判断 @ popover 里是否有候选成员（ListItem ≥ 1）。
        无候选时 Enter 会被微信当普通消息发送，必须提前判定失败。
        """
        try:
            for ctrl, _depth in auto.WalkControl(window, maxDepth=10):
                if ctrl.ClassName != 'mmui::XPopover' or not ctrl.Exists(0):
                    continue
                for sub, _d in auto.WalkControl(ctrl, maxDepth=8):
                    if sub.ControlTypeName == 'ListItemControl' and sub.Exists(0):
                        return True
            return False
        except Exception as e:
            if config.DEBUG:
                logger.debug(f"_at_popover_has_candidate 异常: {e}")
            return False

    def _wait_at_popover(self, window, baseline: int) -> bool:
        """轮询等待 popover 数量超过 baseline（即新 popover 出现），超时返回 False。"""
        deadline = time.monotonic() + self._AT_POPOVER_TIMEOUT
        while True:
            if self._count_at_popovers(window) > baseline:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(self._AT_POPOVER_INTERVAL)

    def _wait_at_popover_candidate(self, window) -> bool:
        """轮询等待 popover 出现候选成员（ListItem ≥ 1），超时返回 False。"""
        deadline = time.monotonic() + self._AT_POPOVER_TIMEOUT
        while True:
            if self._at_popover_has_candidate(window):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(self._AT_POPOVER_INTERVAL)

    def _wait_at_popover_gone(self, window, baseline: int) -> bool:
        """轮询等待 popover 数量回落到 baseline，超时返回 False。"""
        deadline = time.monotonic() + self._AT_POPOVER_GONE_TIMEOUT
        while True:
            if self._count_at_popovers(window) <= baseline:
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(self._AT_POPOVER_INTERVAL)

    @staticmethod
    def _cleanup_temp_path(
        temp_path: str | None,
        temp_dir: str | None,
        *,
        initial_delay: float = 1.0,
        retry_delay: float = 1.0,
        retries: int = 3,
    ):
        if temp_path:
            time.sleep(initial_delay)
            for _ in range(retries):
                try:
                    os.unlink(temp_path)
                    break
                except PermissionError:
                    time.sleep(retry_delay)
                except Exception:
                    break
        if temp_dir:
            with contextlib.suppress(Exception):
                os.rmdir(temp_dir)

    def _schedule_temp_path_cleanup(self, temp_path: str | None, temp_dir: str | None, *, initial_delay: float):
        if not temp_path and not temp_dir:
            return
        threading.Thread(
            target=self._cleanup_temp_path,
            kwargs={
                "temp_path": temp_path,
                "temp_dir": temp_dir,
                "initial_delay": initial_delay,
                "retry_delay": self._DROPFILE_CLEANUP_RETRY_DELAY,
                "retries": self._DROPFILE_CLEANUP_RETRIES,
            },
            daemon=True,
            name="wechat-temp-cleanup",
        ).start()

    def find_all_wechat_windows(self) -> list[DiscoveredWindow]:
        """查找所有微信窗口(包括弹出的群聊窗口)."""
        self._ensure_uia()
        logger.debug("正在扫描所有微信窗口...")
        all_windows: list[DiscoveredWindow] = []

        # 支持的类名列表
        # mmui::MainWindow - 主窗口
        # mmui::ChatSingleWindow - 弹出的群聊窗口
        # WeChatMainWndForPC - 旧版微信主窗口
        class_names = ["mmui::MainWindow", "mmui::ChatSingleWindow", "WeChatMainWndForPC"]
        main_titles = ["微信", "WeChat", "Weixin"]

        # 遍历所有可能的类名
        for cls in class_names:
            try:
                # 获取根控件并查找所有匹配的窗口
                root = auto.GetRootControl()
                controls = root.GetChildren()

                for ctrl in controls:
                    try:
                        if ctrl.ClassName == cls and ctrl.Exists(0):
                            title = ctrl.Name
                            # 跳过空标题
                            if not title:
                                continue

                            all_windows.append(self._discovered_window(ctrl))
                            logger.debug(f"发现窗口: {title} ({cls})")
                    except Exception:
                        continue
            except Exception as e:
                logger.warning(f"扫描类名 {cls} 时出错: {e}")
                continue

        # 过滤掉主窗口(标题为"微信"或"WeChat")
        popup_windows = [
            w for w in all_windows
            if w.title not in main_titles
        ]

        logger.debug(f"扫描到 {len(popup_windows)} 个群聊窗口")
        return popup_windows

    @staticmethod
    def _discovered_window(control) -> DiscoveredWindow:
        return DiscoveredWindow(
            title=control.Name,
            window=control,
            debug_info=WindowsGroupDebugInfo(
                window_id=WindowsWeChatAdapter._native_window_handle(control),
                automation_id=WindowsWeChatAdapter._automation_id(control),
            ),
        )

    @staticmethod
    def _native_window_handle(control) -> int | None:
        try:
            window_id = int(control.NativeWindowHandle)
        except Exception:
            return None
        return window_id or None

    @staticmethod
    def _automation_id(control) -> str | None:
        try:
            automation_id = control.AutomationId
        except Exception:
            return None
        return automation_id if isinstance(automation_id, str) and automation_id else None

    def activate_specific_window(self, window):
        """激活指定的微信窗口(调用方须持有 _send_lock)

        Args:
            window: WindowControl 对象
        """
        logger.debug(f"正在激活窗口: {window.Name}")
        try:
            import ctypes

            import win32gui

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            hwnd = window.NativeWindowHandle
            current_thread = kernel32.GetCurrentThreadId()
            foreground_thread = user32.GetWindowThreadProcessId(user32.GetForegroundWindow(), None)
            target_thread = user32.GetWindowThreadProcessId(hwnd, None)
            attached = []
            try:
                for thread_id in {foreground_thread, target_thread} - {current_thread, 0}:
                    if user32.AttachThreadInput(current_thread, thread_id, True):
                        attached.append(thread_id)
                if user32.IsIconic(hwnd):
                    user32.ShowWindow(hwnd, 9)
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            finally:
                for thread_id in attached:
                    user32.AttachThreadInput(current_thread, thread_id, False)
            time.sleep(0.3)
            if win32gui.GetForegroundWindow() != hwnd:
                raise RuntimeError("目标窗口未获得前台焦点")
            logger.debug(f"窗口已激活: {window.Name}")
            return True
        except Exception as e:
            logger.debug(f"激活窗口时出现警告: {e}")
            return False

    def get_messages_from_window(self, window) -> list[ReceivedMessage]:
        """从指定窗口获取消息

        Args:
            window: WindowControl 对象

        Returns:
            list[str]: 消息列表
        """
        self._ensure_uia()
        if config.WINDOWS_MESSAGE_READER == "chatlog":
            return self._db_reader.read(window)
        state_key = self._message_state_key(window)
        # 读取不需要锁:每个线程有独立 UIA/COM 上下文,读不同窗口可并行
        logger.debug("正在查找消息列表...")
        # 方案1:通过名称查找
        msg_list = window.ListControl(Name="消息")
        if not msg_list.Exists(0.5):
            logger.debug("未找到名为'消息'的列表,尝试查找任意 ListControl...")
            # 方案2:查找第一个 ListControl
            msg_list = window.ListControl()

        if not msg_list.Exists(0):
            logger.warning("在当前窗口中未找到任何消息列表控件.")
            with self._message_state_lock:
                self._message_states.setdefault(state_key, _MessageState()).observed = []
            return []

        logger.debug("成功定位消息列表,正在提取消息...")
        records: list[_MessageRecord] = []
        try:
            # 遍历消息列表的子项
            for item in msg_list.GetChildren():
                # 新版微信的消息内容在子控件的 Name 属性里
                if item.ControlTypeName != 'ListItemControl':
                    continue
                text_control = item.TextControl()
                content = text_control.Name if text_control.Exists(0) else item.Name
                class_name = item.ClassName
                records.append(_MessageRecord(
                    message=(ReceivedMessage(content=content) if self._triggerable_message(content or "", class_name or "") else None),
                    runtime_id=self._message_runtime_id(item),
                    fingerprint=self._message_fingerprint(content or "", class_name or ""),
                ))

            with self._message_state_lock:
                self._message_states.setdefault(state_key, _MessageState()).observed = records
            return [record.message for record in records if record.message is not None]
        except Exception as e:
            with self._message_state_lock:
                self._message_states.setdefault(state_key, _MessageState()).observed = []
            logger.error(f"提取消息时出错: {e}")
            return []

    @staticmethod
    def _find_reply_message(items, reply_to_message: str):
        if not reply_to_message.strip():
            return None
        candidates = []
        for item in reversed(items):
            if item.ClassName not in {"mmui::ChatTextItemView", "mmui::ChatBubbleReferItemView"}:
                continue
            raw = item.Name
            bodies = [raw]
            match = _QUOTE_RE.search(raw)
            if (match and match.start() > 0 and raw[match.start() - 1] == "\n"
                    and match.group(1).strip() and match.group(2).strip()):
                bodies.append(raw[:match.start()].strip())
            candidates.append((item, bodies))
        for item, bodies in candidates:
            if reply_to_message in bodies:
                return item
        normalized = _normalize_message(reply_to_message)
        return next((item for item, bodies in candidates
                     if any(_normalize_message(body) == normalized for body in bodies)), None)

    @staticmethod
    def _reply_preview(window):
        preview = window.GroupControl(searchDepth=25, ClassName="mmui::ComposeReferView")
        return preview if preview.Exists(0) and not preview.IsOffscreen else None

    @staticmethod
    def _reply_menus(window):
        return [c for c in window.GetChildren() if c.ClassName == "mmui::XMenu" and not c.IsOffscreen]

    @staticmethod
    def _wait_for_reply(predicate):
        deadline = time.monotonic() + 1.5
        while True:
            result = predicate()
            if result:
                return result
            if time.monotonic() >= deadline:
                return None
            time.sleep(0.05)

    @staticmethod
    def _reply_state_key(window) -> tuple[int, str]:
        target = window.EditControl(AutomationId="chat_input_field")
        if not target.Exists(0) or not target.Name:
            raise RuntimeError("无法确认引用所属聊天")
        return window.NativeWindowHandle, target.Name

    def clear_reply(self, window, key: tuple[int, str] | None = None) -> bool:
        try:
            if key is not None:
                if self._reply_state_key(window) != key:
                    return False
                self._wait_for_reply(lambda: self._reply_preview(window) is None)
                if self._reply_state_key(window) != key:
                    return False
            preview = self._reply_preview(window)
            if preview is None:
                return True
            key = key or self._reply_state_key(window)
            button = preview.ButtonControl(searchDepth=2, Name="删除引用消息")
            if not button.Exists(0) or not button.IsEnabled or button.IsOffscreen:
                raise RuntimeError("无法确认取消引用按钮")
            if not self.activate_specific_window(window):
                raise RuntimeError("无法激活窗口清理引用")
            if self._reply_state_key(window) != key:
                return False
            button.Click(waitTime=0.05)
            if not self._wait_for_reply(lambda: self._reply_preview(window) is None):
                raise RuntimeError("引用预览未消失")
            return self._reply_state_key(window) == key
        except Exception as e:
            logger.warning("清理引用失败: %s", e)
            return False

    def clear_input(self, window):
        target = self._focus_send_target(window)
        target.SendKeys('{CTRL}a{DELETE}', waitTime=0.2)
        return target

    @contextlib.contextmanager
    def reply_context(self, window, reply_to_message: str | None):
        if not reply_to_message:
            yield False
            return
        if self._reply_preview(window) is not None:
            raise RuntimeError("输入框已有引用草稿,停止发送")
        message_list = window.ListControl(AutomationId="chat_message_list")
        if not message_list.Exists(0):
            raise RuntimeError("无法读取消息列表,停止发送")
        item = self._find_reply_message(message_list.GetChildren(), reply_to_message)
        if item is None or item.IsOffscreen:
            yield False
            return
        if self._reply_menus(window):
            raise RuntimeError("微信已有打开的菜单,停止引用操作")
        key = self._reply_state_key(window)

        import ctypes

        handle = window.NativeWindowHandle
        scale = ctypes.windll.user32.GetDpiForWindow(handle) / 96
        if scale <= 0:
            raise RuntimeError("无法读取窗口缩放比例")
        rect, viewport = item.BoundingRectangle, message_list.BoundingRectangle
        original_id, original_text = item.GetRuntimeId(), item.Name
        # UIA 暴露整行矩形;按 DPI 换算气泡两侧的位置,以消息菜单确认是否命中。
        xs = (round(rect.right - 90 * scale), round(rect.left + 90 * scale))
        ys = dict.fromkeys((round(rect.top + 28 * scale), round(rect.bottom - 28 * scale)))
        clicked = False
        try:
            for y in ys:
                for x in xs:
                    if not (max(rect.left, viewport.left) < x < min(rect.right, viewport.right)
                            and max(rect.top, viewport.top) < y < min(rect.bottom, viewport.bottom)):
                        continue
                    current_rect = item.BoundingRectangle
                    if (item.GetRuntimeId() != original_id or item.Name != original_text
                            or (current_rect.left, current_rect.top, current_rect.right, current_rect.bottom)
                            != (rect.left, rect.top, rect.right, rect.bottom)):
                        raise RuntimeError("待引用消息已变化,停止发送")
                    if ctypes.windll.user32.GetForegroundWindow() != handle:
                        raise RuntimeError("目标窗口失去焦点,停止引用")
                    auto.RightClick(x, y, waitTime=0.05)
                    menus = self._wait_for_reply(lambda: self._reply_menus(window))
                    if not menus:
                        continue
                    if len(menus) != 1:
                        raise RuntimeError("未找到唯一消息菜单,停止发送")
                    options = [c for c in menus[0].GetChildren()
                               if c.ControlTypeName == "MenuItemControl" and c.Name == "引用" and c.IsEnabled]
                    if not options:
                        menus[0].SendKeys("{ESC}", waitTime=0.05)
                        continue
                    if (len(options) != 1 or item.GetRuntimeId() != original_id or item.Name != original_text
                            or self._reply_state_key(window) != key):
                        raise RuntimeError("待引用消息或菜单已变化,停止发送")
                    clicked = True
                    options[0].Click(waitTime=0.05)
                    if not self._wait_for_reply(lambda: self._reply_preview(window)):
                        raise RuntimeError("未确认引用预览出现,停止发送")
                    if self._reply_state_key(window) != key:
                        raise RuntimeError("引用时聊天已切换,停止发送")
                    yield True
                    return
            raise RuntimeError("无法打开原消息的引用菜单,停止发送")
        finally:
            try:
                for menu in self._reply_menus(window):
                    menu.SendKeys("{ESC}", waitTime=0.05)
            except Exception as e:
                logger.warning("关闭引用菜单失败: %s", e)
            if clicked:
                self.clear_reply(window, key)

    def send_text_to_window(self, window, text: str) -> bool:
        """向指定窗口发送文本消息

        Args:
            window: WindowControl 对象
            text: 要发送的文本

        Returns:
            bool: 是否发送成功
        """
        self._ensure_uia()
        with self._send_lock:
            logger.debug(f"[send_text_to_window] 向窗口 {window.Name} 发送: {text[:20]}...")

            # 激活窗口
            if not self.activate_specific_window(window):
                return False
            try:
                target = self.clear_input(window)

                # 开头 @ 名字逐个走「@ → 等新 popover → 粘名字 → Enter → 等 popover 关闭」
                # popover 未弹/异常则清空，fallback 整条原文按字面发出
                at_names, remaining = self._parse_at_prefix(text)
                at_ok = True
                try:
                    # baseline 锚定进入 @ 流程前已存在的 popover 数量，
                    # 避免无关 popover（表情/右键菜单/残影）造成 false positive
                    baseline = self._count_at_popovers(window)
                    for name in at_names:
                        target.SendKeys('@', waitTime=0.05)
                        if not self._wait_at_popover(window, baseline):
                            logger.warning(f"[send_text_to_window] @ popover 未出现: {name}")
                            at_ok = False
                            break
                        self.set_clipboard_text_verified(name)
                        target.SendKeys('{CTRL}v', waitTime=0.05)
                        # 等候选过滤完成；无候选时按 Enter 会发出字面消息，必须提前判定失败
                        if not self._wait_at_popover_candidate(window):
                            logger.warning(f"[send_text_to_window] @ popover 无候选: {name}")
                            at_ok = False
                            break
                        target.SendKeys('{ENTER}', waitTime=0.05)
                        # 等 popover 关闭再处理下一个 @，防止下轮 _wait_at_popover 撞残影
                        self._wait_at_popover_gone(window, baseline)
                except Exception as e:
                    logger.warning(f"[send_text_to_window] @ 流程异常，fallback 原文发送: {e}")
                    at_ok = False
                if not at_ok:
                    target.SendKeys('{CTRL}a{DELETE}', waitTime=0.1)
                    remaining = text

                if remaining:
                    # 通过剪贴板设置文本
                    auto.SetClipboardText(remaining)

                    # 验证剪贴板内容
                    clipboard_content = auto.GetClipboardText()
                    if clipboard_content != remaining:
                        if config.DEBUG:
                            logger.debug(f"剪贴板截断!原始长度={len(remaining)} 剪贴板长度={len(clipboard_content)}")
                            logger.debug(f"原始内容: {remaining!r}")
                            logger.debug(f"剪贴板内容: {clipboard_content!r}")

                        # 使用 Windows API 重新设置
                        self._set_clipboard_text_windows_api(remaining)

                        if config.DEBUG:
                            logger.debug("已使用 win32clipboard 重新设置剪贴板")

                    # 粘贴
                    target.SendKeys('{CTRL}v', waitTime=0.2)

                # 发送消息
                token = self._sent_messages.record(self._message_state_key(window), text)
                try:
                    target.SendKeys('{ENTER}', waitTime=0.2)
                except Exception:
                    self._sent_messages.discard(token)
                    raise
                logger.debug(f"✓ 发送成功到 [{window.Name}]: {text[:20]}...")
                return True
            except Exception as e:
                logger.error(f"发送消息失败: {e}")
                return False

    def send_image_to_window(self, window, image_base64: str) -> bool:
        """向指定窗口发送图片(base64编码)

        Args:
            window: WindowControl 对象
            image_base64: base64 编码的图片数据

        Returns:
            bool: 是否发送成功
        """
        import base64
        import os
        import struct
        import tempfile

        import win32clipboard

        self._ensure_uia()
        with self._send_lock:
            logger.debug(f"[send_image_to_window] 向窗口 {window.Name} 发送图片...")

            # 激活窗口
            if not self.activate_specific_window(window):
                return False

            temp_path = None
            try:
                self.clear_input(window)
                image_data = base64.b64decode(image_base64)

                suffix = '.png'
                if image_data[:2] == b'\xff\xd8':
                    suffix = '.jpg'
                elif image_data[:4] == b'GIF8':
                    suffix = '.gif'
                elif image_data[:4] == b'RIFF' and image_data[8:12] == b'WEBP':
                    suffix = '.webp'

                fd, temp_path = tempfile.mkstemp(suffix=suffix)
                with os.fdopen(fd, 'wb') as f:
                    f.write(image_data)

                temp_path_bytes = temp_path.encode('utf-16-le') + b'\x00\x00'
                dropfiles = struct.pack('IIIIi', 20, 0, 0, 0, 1) + temp_path_bytes + b'\x00\x00'
                self._set_clipboard_data(win32clipboard.CF_HDROP, dropfiles)

                time.sleep(0.3)

                window.SendKeys('{CTRL}v', waitTime=0.5)
                window.SendKeys('{ENTER}', waitTime=0.2)

                logger.debug(f"✓ 图片发送成功到 [{window.Name}]")
                return True
            except Exception as e:
                logger.error(f"发送图片失败: {e}")
                return False
            finally:
                if temp_path:
                    self._schedule_temp_path_cleanup(
                        temp_path,
                        None,
                        initial_delay=self._DROPFILE_CLEANUP_DELAY,
                    )

    def send_file_to_window(self, window, file_base64: str, filename: str) -> bool:
        """向指定窗口发送文件(base64 编码)"""
        import base64

        self._ensure_uia()
        with self._send_lock:
            logger.debug(f"[send_file_to_window] 向窗口 {window.Name} 发送文件: {filename}")

            if not self.activate_specific_window(window):
                return False
            temp_path = None
            temp_dir = None
            try:
                target = self.clear_input(window)
                file_data = base64.b64decode(file_base64)
                temp_path, temp_dir = self._write_temp_file(file_data, filename=filename, default_name="attachment.bin")
                self._paste_file_to_window(window, temp_path)
                self._send_file_paste_and_enter(target, self._message_state_key(window), filename)
                self._schedule_temp_path_cleanup(temp_path, temp_dir, initial_delay=self._DROPFILE_CLEANUP_DELAY)
                temp_path = None
                temp_dir = None

                logger.debug(f"✓ 文件发送成功到 [{window.Name}]: {filename}")
                return True
            except Exception as e:
                logger.error(f"发送文件失败: {e}")
                return False
            finally:
                if temp_path or temp_dir:
                    self._schedule_temp_path_cleanup(temp_path, temp_dir, initial_delay=1.0)

    def send_voice_to_window(
        self,
        window,
        file_base64: str,
        filename: str,
        timeout: float | None = None,
    ) -> bool:
        """通过 VB-CABLE 向 Windows 微信发送 Base64 MP3 语音。"""
        from gateway.utils.windows_voice import prepare_voice_file

        self._ensure_uia()
        logger.debug(f"[send_voice_to_window] 向窗口 {window.Name} 发送语音: {filename}")
        deadline = time.monotonic() + (config.WINDOWS_VOICE_TIMEOUT if timeout is None else timeout)
        try:
            pcm, output_device, duration = prepare_voice_file(file_base64, filename)
        except Exception as e:
            logger.error(f"读取语音文件失败: {e}")
            return False
        return self._send_voice_pcm_to_window(window, pcm, output_device, duration, deadline)

    def _send_voice_pcm_to_window(self, window, pcm, output_device: int, duration: float, deadline: float) -> bool:
        import sounddevice as sd

        remaining = deadline - time.monotonic()
        if remaining <= 0 or not self._send_lock.acquire(timeout=remaining):
            logger.error("发送语音失败: 等待全局发送锁超时")
            return False
        try:
            if not self.activate_specific_window(window):
                raise RuntimeError("激活目标微信窗口失败")
            self.clear_input(window)
            finish_delay = max(
                self._VOICE_RECORDING_FINISH_DELAY,
                self._VOICE_MIN_RECORDING_DURATION - self._VOICE_RECORDING_START_DELAY - duration,
            )
            remaining = deadline - time.monotonic()
            if remaining < self._VOICE_RECORDING_START_DELAY + duration + finish_delay:
                raise TimeoutError("语音发送总超时不足以完成播放")
            auto.PressKey(self._VOICE_ALT_KEY, waitTime=0)
            try:
                time.sleep(self._VOICE_RECORDING_START_DELAY)
                timed_out = threading.Event()
                timer = threading.Timer(
                    max(0.0, deadline - time.monotonic()),
                    lambda: (timed_out.set(), sd.stop()),
                )
                timer.start()
                try:
                    sd.play(pcm.reshape(-1, 1), config.WINDOWS_VOICE_SAMPLE_RATE, device=output_device, blocking=True)
                finally:
                    timer.cancel()
                if timed_out.is_set():
                    raise TimeoutError("音频播放超时")
                time.sleep(finish_delay)
            finally:
                auto.ReleaseKey(self._VOICE_ALT_KEY, waitTime=0)
            logger.debug(f"✓ 语音发送成功到 [{window.Name}]: {duration:.1f}s")
            return True
        except Exception as e:
            logger.error(f"发送语音失败: {e}")
            return False
        finally:
            self._send_lock.release()


class WindowsChatlogReader:
    def __init__(self, adapter):
        self.adapter = adapter
        host = config.CHATLOG_HTTP_HOST if config.CHATLOG_HTTP_HOST != "0.0.0.0" else "127.0.0.1"
        self.client = httpx.Client(
            base_url=f"http://{host}:{config.CHATLOG_HTTP_PORT}/api/chatlog/v2/",
            headers={"Authorization": f"Bearer {config.HTTP_API_TOKEN}"} if config.HTTP_API_TOKEN else {},
            timeout=30,
        )
        self.talkers = {}
        self.cursors = {}
        self.seen = {}

    def read(self, window):
        key = self.adapter._message_state_key(window)
        talker = self.talkers.get(key)
        if talker is None:
            talker = self.adapter.standalone_conversation_id(self.adapter._automation_id(window))
            if not talker:
                response = self.client.get("groups", params={"limit": 0})
                response.raise_for_status()
                matches = {g["username"] for g in response.json()
                           if window.Name in {g.get(k) for k in ("username", "display_name", "remark", "nick_name")}}
                if len(matches) != 1:
                    raise RuntimeError(f"[{window.Name}] 无法唯一匹配 Windows Chatlog 会话")
                talker = matches.pop()
            self.talkers[key] = talker
        cursor = self.cursors.get(key)
        if cursor is None:
            response = self.client.get("messages", params={"group": talker, "limit": 20, "desc": True})
            response.raise_for_status()
            rows = list(reversed(response.json()))
        else:
            rows = []
            offset = 0
            while True:
                response = self.client.post("messages/batch", json={
                    "items": [{"group": talker, "since_seq": cursor}], "limit": 100, "offset": offset,
                })
                response.raise_for_status()
                page = response.json()["results"][talker]
                rows.extend(page)
                if len(page) < 100 or any(row["seq"] > cursor for row in page):
                    break
                offset += len(page)
        result = []
        known = self.seen.get(key, set())
        identities = set()
        latest = cursor or 0
        for row in rows:
            sequence = int(row["seq"])
            identity = (sequence, int(row.get("server_id", 0)), int(row.get("local_id", 0)))
            identities.add(identity)
            latest = max(latest, sequence)
            if identity in known or row["is_self"] or (cursor is not None and sequence < cursor):
                continue
            content = row["content"]
            if row["msg_type"] == 49:
                try:
                    content = ET.fromstring(content).findtext(".//title", "").strip()
                except ET.ParseError:
                    continue
            elif row["msg_type"] != 1:
                continue
            if content:
                result.append(ReceivedMessage(content=content, sender=row.get("sender", ""),
                                              sender_name=row.get("sender_name", ""), at_users=row.get("at_users", [])))
        self.cursors[key] = latest
        self.seen[key] = {identity for identity in known | identities if identity[0] >= latest}
        return result
