"""Gateway 运行时 - 自动发现窗口,管理 Detector/Sender/WS 网关"""
import logging
import threading
import time
from typing import Any

from gateway.adapters import get_wechat_adapter
from gateway.adapters.base import BaseWeChatAdapter, DiscoveredWindow
from gateway.console.adapters import get_console_adapter
from gateway.console.adapters.base import BaseConsoleAdapter
from gateway.core.detector import MessageDetector
from gateway.core.gateway import Gateway
from gateway.core.sender import MessageSender
from gateway.metrics import metrics
from gateway.models import GroupInfo
from gateway.startup import WeChatStartup

logger = logging.getLogger(__name__)

SCAN_INTERVAL = 5


class GatewayRuntime:
    def __init__(self):
        self.adapter: BaseWeChatAdapter = get_wechat_adapter()
        self.console_adapter: BaseConsoleAdapter | None = get_console_adapter(self.adapter)
        self.wechat_startup = WeChatStartup(self.adapter, self.console_adapter)
        self.group_windows: dict[str, Any] = {}
        self._group_snapshots: dict[str, GroupInfo] = {}
        self.gateway = Gateway(MessageSender(self.adapter, self.group_windows))
        self._lock = threading.Lock()
        self._detectors: dict[str, _DetectorHandle] = {}
        self._running = False
        metrics.bind("groups_active", lambda: len(self.groups))
        metrics.bind("message_queue_depth", self.gateway.gateway_queue.qsize)
        metrics.bind("send_queue_depth", self.gateway.sender.send_queue.qsize)
        logger.info("Gateway 初始化完成")

    @property
    def groups(self) -> list[str]:
        with self._lock:
            return list(self._detectors.keys())

    @property
    def group_snapshots(self) -> list[GroupInfo]:
        with self._lock:
            return [snapshot.model_copy() for snapshot in self._group_snapshots.values()]

    def start(self):
        self._running = True
        self.gateway.start()
        self.wechat_startup.start()
        threading.Thread(target=self._scan_loop, daemon=True, name="window-scanner").start()
        logger.info("Gateway 已启动")

    def stop(self):
        self._running = False
        self.wechat_startup.stop()
        with self._lock:
            for handle in self._detectors.values():
                handle.detector.stop()
            self._detectors.clear()
            self.group_windows.clear()
            self._group_snapshots.clear()
        self.gateway.stop()
        logger.info("Gateway 已停止")

    def _scan_loop(self):
        while self._running:
            try:
                self._sync_windows()
                metrics.increment("window_scans_total", result="success")
            except Exception as e:
                metrics.increment("window_scans_total", result="error")
                logger.error(f"窗口扫描出错: {e}")
            time.sleep(SCAN_INTERVAL)

    def _sync_windows(self):
        discovered = {item.title: item for item in self.adapter.find_all_wechat_windows()}

        with self._lock:
            for title, handle in list(self._detectors.items()):
                item = discovered.get(title)
                previous = self._group_snapshots[title]
                if item is not None and handle.thread.is_alive() and previous.debug_info == item.debug_info:
                    continue
                handle.detector.stop()
                del self._detectors[title]
                self.group_windows.pop(title, None)
                self._group_snapshots.pop(title, None)
                logger.info(f"窗口已关闭或更新: {title}")

            for title, item in discovered.items():
                if title not in self._detectors:
                    self.group_windows[title] = item.window
                    self._detectors[title] = self._create_detector(title, item.window)
                    logger.info(f"发现新窗口: {title}")
                self._group_snapshots[title] = self._group_snapshot(item)

    def _group_snapshot(self, window: DiscoveredWindow) -> GroupInfo:
        return GroupInfo(name=window.title, active=True, debug_info=window.debug_info)

    def _create_detector(self, group_name: str, window) -> "_DetectorHandle":
        detector = MessageDetector(
            group_name=group_name,
            window=window,
            wechat_adapter=self.adapter,
            gateway_queue=self.gateway.gateway_queue,
        )
        thread = threading.Thread(target=detector.run, daemon=True, name=f"detector-{group_name}")
        thread.start()
        return _DetectorHandle(detector, thread)


class _DetectorHandle:
    __slots__ = ("detector", "thread")

    def __init__(self, detector: MessageDetector, thread: threading.Thread):
        self.detector = detector
        self.thread = thread
