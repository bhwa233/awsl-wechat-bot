import threading
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from gateway.adapters.base import DiscoveredWindow
from gateway.models import GroupDebugInfo
from gateway.runtime import GatewayRuntime


class WindowInfo(GroupDebugInfo):
    window_id: int


class WindowRefreshTests(unittest.TestCase):
    def setUp(self):
        self.runtime = GatewayRuntime.__new__(GatewayRuntime)
        self.runtime.adapter = MagicMock()
        self.runtime._lock = threading.Lock()
        self.old_window = object()
        self.info = WindowInfo(window_id=10)
        self.handle = SimpleNamespace(detector=MagicMock(), thread=MagicMock())
        self.handle.thread.is_alive.return_value = True
        self.runtime._detectors = {"测试群": self.handle}
        self.runtime.group_windows = {"测试群": self.old_window}
        self.runtime._group_snapshots = {"测试群": self.runtime._group_snapshot(
            DiscoveredWindow("测试群", self.old_window, self.info))}
        self.runtime._create_detector = MagicMock(return_value=SimpleNamespace(detector=MagicMock(), thread=MagicMock()))

    def test_same_title_new_window_replaces_sender_and_detector(self):
        new_window = object()
        info = WindowInfo(window_id=20)
        self.runtime.adapter.find_all_wechat_windows.return_value = [DiscoveredWindow("测试群", new_window, info)]
        self.runtime._sync_windows()
        self.handle.detector.stop.assert_called_once()
        self.assertIs(self.runtime.group_windows["测试群"], new_window)
        self.runtime._create_detector.assert_called_once_with("测试群", new_window)
        self.assertEqual(self.runtime._group_snapshots["测试群"].debug_info.window_id, 20)

    def test_unchanged_window_keeps_running_detector(self):
        self.runtime.adapter.find_all_wechat_windows.return_value = [DiscoveredWindow("测试群", object(), self.info)]
        self.runtime._sync_windows()
        self.handle.detector.stop.assert_not_called()
        self.runtime._create_detector.assert_not_called()

    def test_missing_window_removes_even_failed_but_live_detector(self):
        self.runtime.adapter.find_all_wechat_windows.return_value = []
        self.runtime._sync_windows()
        self.handle.detector.stop.assert_called_once()
        self.assertEqual(self.runtime.group_windows, {})
        self.assertEqual(self.runtime._detectors, {})

    def test_dead_detector_is_recreated_in_same_scan(self):
        self.handle.thread.is_alive.return_value = False
        self.runtime.adapter.find_all_wechat_windows.return_value = [DiscoveredWindow("测试群", self.old_window, self.info)]
        self.runtime._sync_windows()
        self.runtime._create_detector.assert_called_once()
