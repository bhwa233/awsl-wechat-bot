import contextlib
import logging
import os
import re
import threading
import time
from typing import ClassVar

logging.getLogger("comtypes").setLevel(logging.WARNING)

import uiautomation as auto  # noqa: E402

from common.config import config  # noqa: E402
from gateway.adapters.base import BaseWeChatAdapter  # noqa: E402

logger = logging.getLogger(__name__)


class WindowsWeChatAdapter(BaseWeChatAdapter):
    SUPPORTS_VOICE = True
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
        self._send_lock = threading.Lock()  # 仅发送需要锁(激活窗口+剪贴板+按键)
        self._thread_inits: dict[int, auto.UIAutomationInitializerInThread] = {}

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

    @staticmethod
    def _send_file_paste_and_enter(target):
        time.sleep(0.3)
        target.SendKeys('{CTRL}v', waitTime=0.5)
        time.sleep(1.0)
        target.SendKeys('{ENTER}', waitTime=0.5)

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

    def find_all_wechat_windows(self) -> list[dict]:
        """查找所有微信窗口(包括弹出的群聊窗口)

        Returns:
            list[dict]: 窗口信息列表,每个字典包含:
                - title: 窗口标题(群名)
                - window: WindowControl 对象
                - class: 窗口类名
        """
        self._ensure_uia()
        logger.debug("正在扫描所有微信窗口...")
        all_windows = []

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

                            all_windows.append({
                                "title": title,
                                "window": ctrl,
                                "class": cls
                            })
                            logger.debug(f"发现窗口: {title} ({cls})")
                    except Exception:
                        continue
            except Exception as e:
                logger.warning(f"扫描类名 {cls} 时出错: {e}")
                continue

        # 过滤掉主窗口(标题为"微信"或"WeChat")
        popup_windows = [
            w for w in all_windows
            if w["title"] not in main_titles
        ]

        logger.debug(f"扫描到 {len(popup_windows)} 个群聊窗口")
        return popup_windows

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

    def get_messages_from_window(self, window) -> list[str]:
        """从指定窗口获取消息

        Args:
            window: WindowControl 对象

        Returns:
            list[str]: 消息列表
        """
        self._ensure_uia()
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
            return []

        logger.debug("成功定位消息列表,正在提取消息...")
        messages = []
        try:
            # 遍历消息列表的子项
            for item in msg_list.GetChildren():
                # 新版微信的消息内容在子控件的 Name 属性里
                if item.ControlTypeName == 'ListItemControl':
                    if item.ClassName == 'mmui::ChatVoiceItemView':
                        continue
                    text_control = item.TextControl()
                    # 有些消息是图片或表情,没有文本控件
                    if text_control.Exists(0):
                         messages.append(text_control.Name)
                    else: # 尝试直接获取 ListItemControl 的 Name
                        messages.append(item.Name)

            # 过滤噪音
            filtered_messages = []
            for text in messages:
                if not text or len(text) < 2:
                    continue
                if text in ['[图片]', '[表情]', '[视频]', '[文件]', 'Animated Stickers']:
                    continue
                if re.match(r'^[\d:]+$', text):  # 纯时间戳
                    continue
                filtered_messages.append(text)

            return filtered_messages
        except Exception as e:
            logger.error(f"提取消息时出错: {e}")
            return []

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
            target = self._focus_send_target(window)

            try:
                # 清空可能存在的草稿
                target.SendKeys('{CTRL}a{DELETE}', waitTime=0.2)

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
                target.SendKeys('{ENTER}', waitTime=0.2)

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
            target = self._focus_send_target(window)

            temp_path = None
            temp_dir = None
            try:
                file_data = base64.b64decode(file_base64)
                temp_path, temp_dir = self._write_temp_file(file_data, filename=filename, default_name="attachment.bin")
                self._paste_file_to_window(window, temp_path)
                self._send_file_paste_and_enter(target)
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

    def send_voice_to_window(self, window, text: str) -> bool:
        """通过 EdgeTTS + VB-CABLE 向 Windows 微信发送语音。"""
        import sounddevice as sd

        from gateway.utils.windows_voice import prepare_voice

        self._ensure_uia()
        logger.debug(f"[send_voice_to_window] 向窗口 {window.Name} 发送语音: {text[:20]}...")
        deadline = time.monotonic() + config.WINDOWS_VOICE_TIMEOUT
        try:
            pcm, output_device, duration = prepare_voice(text, timeout=config.WINDOWS_VOICE_TIMEOUT)
        except Exception as e:
            logger.error(f"生成语音失败: {e}")
            return False

        remaining = deadline - time.monotonic()
        if remaining <= 0 or not self._send_lock.acquire(timeout=remaining):
            logger.error("发送语音失败: 等待全局发送锁超时")
            return False
        try:
            if not self.activate_specific_window(window):
                raise RuntimeError("激活目标微信窗口失败")
            self._focus_send_target(window)
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
