import time
import logging
import re
import os
import threading

logging.getLogger("comtypes").setLevel(logging.WARNING)

import uiautomation as auto  # noqa: E402
from gateway.adapters.base import BaseWeChatAdapter
from common.config import config

logger = logging.getLogger(__name__)
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}
_WINDOWS_INVALID_FILENAME_RE = re.compile(r'[<>:"/\\\\|?*\x00-\x1f]')
_WINDOWS_DROPFILE_CLEANUP_DELAY = 15.0
_WINDOWS_DROPFILE_CLEANUP_RETRY_DELAY = 1.0
_WINDOWS_DROPFILE_CLEANUP_RETRIES = 10


class WindowsWeChatAdapter(BaseWeChatAdapter):
    def __init__(self):
        self._send_lock = threading.Lock()  # 仅发送需要锁（激活窗口+剪贴板+按键）
        self._thread_inits: dict[int, auto.UIAutomationInitializerInThread] = {}

    def _ensure_uia(self):
        """确保当前线程已初始化 UIA（内部处理 COM 初始化，控件须在同一线程创建和使用）"""
        tid = threading.get_ident()
        if tid not in self._thread_inits:
            self._thread_inits[tid] = auto.UIAutomationInitializerInThread()
            logger.debug(f"线程 {tid} UIA 初始化完成")

    def _set_clipboard_text_windows_api(self, text: str):
        """使用 win32 API 安全设置文本剪贴板。"""
        import win32clipboard
        import win32con

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, text)
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    def _set_clipboard_data(self, format_type: int, data):
        """使用 win32 API 安全设置剪贴板二进制数据。"""
        import win32clipboard

        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardData(format_type, data)
        finally:
            try:
                win32clipboard.CloseClipboard()
            except Exception:
                pass

    @staticmethod
    def _guess_image_filename(image_data: bytes) -> str:
        if image_data[:2] == b'\xff\xd8':
            return "image.jpg"
        if image_data[:4] == b'GIF8':
            return "image.gif"
        if image_data[:4] == b'RIFF' and image_data[8:12] == b'WEBP':
            return "image.webp"
        return "image.png"

    @staticmethod
    def _safe_filename(filename: str | None, default_name: str) -> str:
        safe_name = os.path.basename(filename or "").strip()
        if not safe_name:
            return default_name
        safe_name = _WINDOWS_INVALID_FILENAME_RE.sub("_", safe_name)
        safe_name = safe_name.rstrip(" .")
        if not safe_name:
            return default_name

        stem, ext = os.path.splitext(safe_name)
        if stem.upper() in _WINDOWS_RESERVED_NAMES:
            safe_name = f"_{stem}{ext}"
        return safe_name or default_name

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
            logger.debug(f"[{window.Name}] 未找到 EditControl，回退到窗口级按键")
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
            logger.debug(f"[{window.Name}] 点击输入框失败，继续尝试聚焦: {e}")
        try:
            target.SetFocus()
        except Exception as e:
            logger.debug(f"[{window.Name}] 输入框 SetFocus 失败，继续尝试按键发送: {e}")
        time.sleep(0.2)
        return target

    @staticmethod
    def _send_file_paste_and_enter(target):
        time.sleep(0.3)
        target.SendKeys('{CTRL}v', waitTime=0.5)
        target.SendKeys('{ENTER}', waitTime=0.2)

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
            try:
                os.rmdir(temp_dir)
            except Exception:
                pass

    def _schedule_temp_path_cleanup(self, temp_path: str | None, temp_dir: str | None, *, initial_delay: float):
        if not temp_path and not temp_dir:
            return
        threading.Thread(
            target=self._cleanup_temp_path,
            kwargs={
                "temp_path": temp_path,
                "temp_dir": temp_dir,
                "initial_delay": initial_delay,
                "retry_delay": _WINDOWS_DROPFILE_CLEANUP_RETRY_DELAY,
                "retries": _WINDOWS_DROPFILE_CLEANUP_RETRIES,
            },
            daemon=True,
            name="wechat-temp-cleanup",
        ).start()

    def find_all_wechat_windows(self) -> list[dict]:
        """查找所有微信窗口（包括弹出的群聊窗口）

        Returns:
            list[dict]: 窗口信息列表，每个字典包含：
                - title: 窗口标题（群名）
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

        # 过滤掉主窗口（标题为"微信"或"WeChat"）
        popup_windows = [
            w for w in all_windows
            if w["title"] not in main_titles
        ]

        logger.debug(f"扫描到 {len(popup_windows)} 个群聊窗口")
        return popup_windows

    def activate_specific_window(self, window):
        """激活指定的微信窗口（调用方须持有 _send_lock）

        Args:
            window: WindowControl 对象
        """
        logger.debug(f"正在激活窗口: {window.Name}")
        try:
            try:
                if hasattr(window, "ShowWindow"):
                    window.ShowWindow(1)  # SW_SHOWNORMAL
            except Exception:
                pass
            try:
                if hasattr(window, "Restore"):
                    window.Restore()
            except Exception:
                pass

            window.SetForeground()
            time.sleep(0.5)
            logger.debug(f"窗口已激活: {window.Name}")
        except Exception as e:
            logger.debug(f"激活窗口时出现警告: {e}")

    def get_messages_from_window(self, window) -> list[str]:
        """从指定窗口获取消息

        Args:
            window: WindowControl 对象

        Returns:
            list[str]: 消息列表
        """
        self._ensure_uia()
        # 读取不需要锁：每个线程有独立 UIA/COM 上下文，读不同窗口可并行
        logger.debug("正在查找消息列表...")
        # 方案1：通过名称查找
        msg_list = window.ListControl(Name="消息")
        if not msg_list.Exists(0.5):
            logger.debug("未找到名为'消息'的列表，尝试查找任意 ListControl...")
            # 方案2：查找第一个 ListControl
            msg_list = window.ListControl()

        if not msg_list.Exists(0):
            logger.warning("在当前窗口中未找到任何消息列表控件。")
            return []

        logger.debug("成功定位消息列表，正在提取消息...")
        messages = []
        try:
            # 遍历消息列表的子项
            for item in msg_list.GetChildren():
                # 新版微信的消息内容在子控件的 Name 属性里
                if item.ControlTypeName == 'ListItemControl':
                    text_control = item.TextControl()
                    # 有些消息是图片或表情，没有文本控件
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
            self.activate_specific_window(window)
            target = self._focus_send_target(window)

            try:
                # 清空可能存在的草稿
                target.SendKeys('{CTRL}a{DELETE}', waitTime=0.2)

                # 通过剪贴板设置文本
                auto.SetClipboardText(text)

                # 验证剪贴板内容
                clipboard_content = auto.GetClipboardText()
                if clipboard_content != text:
                    if config.DEBUG:
                        logger.debug(f"剪贴板截断！原始长度={len(text)} 剪贴板长度={len(clipboard_content)}")
                        logger.debug(f"原始内容: {repr(text)}")
                        logger.debug(f"剪贴板内容: {repr(clipboard_content)}")

                    # 使用 Windows API 重新设置
                    self._set_clipboard_text_windows_api(text)

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
        """向指定窗口发送图片（base64编码）

        Args:
            window: WindowControl 对象
            image_base64: base64 编码的图片数据

        Returns:
            bool: 是否发送成功
        """
        import base64
        import tempfile
        import os
        import struct
        import win32clipboard

        self._ensure_uia()
        with self._send_lock:
            logger.info(f"[send_image_to_window] 开始向窗口 {window.Name} 发送图片")

            # 激活窗口
            self.activate_specific_window(window)

            temp_path = None
            try:
                # 解码 base64 数据并保存为临时文件
                image_data = base64.b64decode(image_base64)
                logger.info(f"[send_image_to_window] 图片解码完成: size={len(image_data)} bytes")

                # 根据 magic bytes 检测图片格式
                suffix = '.png'
                if image_data[:2] == b'\xff\xd8':
                    suffix = '.jpg'
                elif image_data[:4] == b'GIF8':
                    suffix = '.gif'
                elif image_data[:4] == b'RIFF' and image_data[8:12] == b'WEBP':
                    suffix = '.webp'
                logger.info(f"[send_image_to_window] 检测图片后缀: {suffix}")

                fd, temp_path = tempfile.mkstemp(suffix=suffix)
                with os.fdopen(fd, 'wb') as f:
                    f.write(image_data)
                logger.info(f"[send_image_to_window] 临时图片已写入: {temp_path}")

                temp_path_bytes = temp_path.encode('utf-16-le') + b'\x00\x00'
                dropfiles = struct.pack('IIIIi', 20, 0, 0, 0, 1) + temp_path_bytes + b'\x00\x00'
                self._set_clipboard_data(win32clipboard.CF_HDROP, dropfiles)
                logger.info("[send_image_to_window] 已写入剪贴板 CF_HDROP")

                time.sleep(0.3)

                # 粘贴图片
                logger.info("[send_image_to_window] 开始发送 Ctrl+V")
                window.SendKeys('{CTRL}v', waitTime=0.5)

                # 发送
                logger.info("[send_image_to_window] 开始发送 Enter")
                window.SendKeys('{ENTER}', waitTime=0.2)

                logger.info(f"[send_image_to_window] 图片发送流程完成: {window.Name}")
                return True
            except Exception as e:
                logger.error(f"发送图片失败: {e}")
                return False
            finally:
                if temp_path:
                    logger.info(f"[send_image_to_window] 准备清理临时图片: {temp_path}")
                    time.sleep(1)
                    for _ in range(3):
                        try:
                            os.unlink(temp_path)
                            logger.info(f"[send_image_to_window] 已清理临时图片: {temp_path}")
                            break
                        except PermissionError:
                            logger.info(f"[send_image_to_window] 临时图片被占用，稍后重试: {temp_path}")
                            time.sleep(1)
                        except Exception:
                            logger.info(f"[send_image_to_window] 临时图片清理跳过: {temp_path}")
                            break

    def send_file_to_window(self, window, file_base64: str, filename: str) -> bool:
        """向指定窗口发送文件（base64 编码）"""
        import base64

        self._ensure_uia()
        with self._send_lock:
            logger.debug(f"[send_file_to_window] 向窗口 {window.Name} 发送文件: {filename}")

            self.activate_specific_window(window)
            target = self._focus_send_target(window)

            temp_path = None
            temp_dir = None
            cleanup_delay = 1.0
            try:
                file_data = base64.b64decode(file_base64)
                temp_path, temp_dir = self._write_temp_file(
                    file_data,
                    filename=filename,
                    default_name="upload.bin",
                )
                self._paste_file_to_window(window, temp_path)
                self._send_file_paste_and_enter(target)
                cleanup_delay = _WINDOWS_DROPFILE_CLEANUP_DELAY
                logger.debug(f"✓ 文件发送成功到 [{window.Name}]: {filename}")
                return True
            except Exception as e:
                logger.error(f"发送文件失败: {e}")
                return False
            finally:
                self._schedule_temp_path_cleanup(temp_path, temp_dir, initial_delay=cleanup_delay)
