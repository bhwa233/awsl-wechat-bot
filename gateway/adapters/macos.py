import time
import subprocess
import os
import logging
import threading
import Quartz
from gateway.adapters.base import BaseWeChatAdapter
from common.config import config
from gateway.utils.ocr import screenshot_and_ocr, get_others_messages

logger = logging.getLogger(__name__)

MAIN_WINDOW_TITLES = {"微信", "WeChat", "Weixin"}


class MacOSWindow:
    """macOS 窗口对象，存储窗口标题、AppleScript 索引和 Quartz 窗口 ID"""

    def __init__(self, title: str, index: int, window_id: int = 0):
        self.title = title
        self.index = index
        self.window_id = window_id  # Quartz kCGWindowNumber

    def Exists(self, timeout: float = 0.5) -> bool:
        return True


class MacOSWeChatAdapter(BaseWeChatAdapter):
    def __init__(self):
        self.process_name = self._detect_wechat_process()
        if not self.process_name:
            raise RuntimeError("微信未运行，请先启动微信")
        self._send_lock = threading.Lock()  # 仅发送需要锁（激活窗口）
        logger.info(f"检测到微信进程: {self.process_name}")

    def _detect_wechat_process(self) -> str | None:
        """检测微信进程名称"""
        for name in ("WeChat", "微信"):
            result = subprocess.run(['pgrep', name], capture_output=True)
            if result.returncode == 0:
                return name
        return None

    def _run_applescript(self, script: str) -> str | None:
        """执行 AppleScript"""
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            timeout=config.APPLESCRIPT_TIMEOUT_LONG
        )
        if result.returncode != 0:
            logger.debug(f"AppleScript 错误: {result.stderr}")
            return None
        return result.stdout.strip()

    def _activate_window_by_index(self, index: int):
        """激活指定索引的微信窗口"""
        script = f'''
        tell application "System Events"
            tell process "{self.process_name}"
                perform action "AXRaise" of window {index}
            end tell
        end tell
        '''
        self._run_applescript(script)
        time.sleep(0.3)

    def _get_quartz_windows(self) -> dict[str, int]:
        """通过 Quartz 获取微信窗口名称→窗口ID 映射"""
        window_list = Quartz.CGWindowListCopyWindowInfo(
            Quartz.kCGWindowListOptionOnScreenOnly | Quartz.kCGWindowListExcludeDesktopElements,
            Quartz.kCGNullWindowID,
        )
        # pgrep 和 Quartz 可能返回不同的进程名（"WeChat" vs "微信"）
        wechat_names = {"WeChat", "微信", "Weixin"}
        result = {}
        for win in window_list:
            owner = win.get(Quartz.kCGWindowOwnerName, "")
            if owner not in wechat_names:
                continue
            title = win.get(Quartz.kCGWindowName, "")
            wid = win.get(Quartz.kCGWindowNumber, 0)
            if title and wid:
                result[title] = wid
        return result

    def find_all_wechat_windows(self) -> list[dict]:
        """枚举所有微信聊天窗口（排除主窗口）"""
        # 从 Quartz 获取窗口 ID 映射
        quartz_map = self._get_quartz_windows()

        script = f'''
        tell application "System Events"
            tell process "{self.process_name}"
                set windowNames to name of every window
                set output to ""
                repeat with i from 1 to count of windowNames
                    if i > 1 then set output to output & "|||"
                    set output to output & (item i of windowNames as text) & ":::" & i
                end repeat
                return output
            end tell
        end tell
        '''
        raw = self._run_applescript(script)
        if not raw:
            return []

        windows = []
        for entry in raw.split("|||"):
            parts = entry.split(":::")
            if len(parts) != 2:
                continue
            title, idx_str = parts[0].strip(), parts[1].strip()
            if not title or title in MAIN_WINDOW_TITLES:
                continue
            try:
                idx = int(idx_str)
            except ValueError:
                continue
            window_id = quartz_map.get(title, 0)
            if not window_id:
                logger.debug(f"窗口 {title} 未找到 Quartz 窗口 ID，跳过")
                continue
            windows.append({"title": title, "window": MacOSWindow(title, idx, window_id)})
            logger.debug(f"发现窗口: {title} (index={idx}, wid={window_id})")

        logger.debug(f"扫描到 {len(windows)} 个聊天窗口")
        return windows

    def get_messages_from_window(self, window: MacOSWindow) -> list[str]:
        """从指定窗口获取消息（Quartz 截图 + OCR，无需激活窗口）"""
        ocr_results = screenshot_and_ocr(window.window_id)
        return get_others_messages(ocr_results)

    def send_text_to_window(self, window: MacOSWindow, text: str) -> bool:
        """向指定窗口发送文本"""
        with self._send_lock:
            return self._send_text(window, text)

    def _send_text(self, window: MacOSWindow, text: str) -> bool:
        self._activate_window_by_index(window.index)
        time.sleep(0.2)
        # 使用 osascript 参数传递文本，避免 AppleScript 字符串注入
        script = '''
        on run argv
            set the clipboard to (item 1 of argv)
            tell application "System Events"
                tell process "''' + self.process_name + '''"
                    keystroke "v" using command down
                    delay 0.3
                    key code 36
                end tell
            end tell
        end run
        '''
        result = subprocess.run(
            ['osascript', '-e', script, text],
            capture_output=True,
            text=True,
            timeout=config.APPLESCRIPT_TIMEOUT_LONG
        )
        if result.returncode != 0:
            logger.debug(f"AppleScript 发送错误: {result.stderr}")
        time.sleep(0.5)
        return True

    def send_image_to_window(self, window: MacOSWindow, image_base64: str) -> bool:
        """向指定窗口发送图片（base64 编码）"""
        import base64
        import tempfile

        with self._send_lock:
            tmp_path = None
            try:
                image_data = base64.b64decode(image_base64)
                with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
                    tmp_file.write(image_data)
                    tmp_path = tmp_file.name

                script = f'''
                set theFile to POSIX file "{tmp_path}"
                try
                    set the clipboard to (read theFile as JPEG picture)
                on error
                    set the clipboard to (read theFile as «class PNGf»)
                end try
                '''
                try:
                    result = subprocess.run(['osascript', '-e', script], capture_output=True, text=True, timeout=config.APPLESCRIPT_TIMEOUT_SHORT)
                except subprocess.TimeoutExpired:
                    logger.error("复制图片到剪贴板超时")
                    return False

                if result.returncode != 0:
                    logger.error(f"复制图片失败: {result.stderr}")
                    return False

                time.sleep(0.3)
                self._activate_window_by_index(window.index)
                time.sleep(0.2)
                script = f'''
                tell application "System Events"
                    tell process "{self.process_name}"
                        keystroke "v" using command down
                        delay 0.5
                        key code 36
                    end tell
                end tell
                '''
                self._run_applescript(script)
                time.sleep(1.0)
                return True
            except Exception as e:
                logger.error(f"发送图片失败: {e}")
                return False
            finally:
                if tmp_path:
                    try:
                        os.unlink(tmp_path)
                    except Exception:
                        pass
