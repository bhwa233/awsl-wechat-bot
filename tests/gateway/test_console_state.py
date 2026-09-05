import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


def load_state_mixin():
    directory = Path(__file__).resolve().parents[2] / "gateway/console/adapters/windows"
    package_name = "gateway.console.adapters.windows"
    package = types.ModuleType(package_name)
    package.__path__ = [str(directory)]
    with patch.dict(sys.modules, {package_name: package}):
        spec = importlib.util.spec_from_file_location(f"{package_name}.state", directory / "state.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module.WindowsStateMixin


WindowsStateMixin = load_state_mixin()


class ConsolePreviewTest(unittest.TestCase):
    def test_pinned_preview_and_relative_time(self):
        node = {
            "name": "测试群01\n已置顶\n[动画表情]\n昨天 18:38\n消息免打扰\n",
            "automation_id": "session_item_测试群01",
            "path": "0.1",
            "rect": {},
        }
        item = WindowsStateMixin()._chat_list_item(node)
        self.assertEqual(item["name"], "测试群01")
        self.assertEqual(item["preview"], "[动画表情]")
        self.assertEqual(item["time"], "昨天 18:38")
        self.assertEqual(item["unread"], 0)
        self.assertTrue(item["muted"])
        self.assertTrue(item["pinned"])
        self.assertEqual(item["raw_text"], node["name"])

    def test_unread_pinned_preview(self):
        raw = "测试群 [2条]\n已置顶\n1\n星期三\n消息免打扰\n"
        self.assertEqual(WindowsStateMixin._preview_from_raw(raw, "测试群"), ("1", "星期三", True))
        self.assertEqual(WindowsStateMixin._unread_count(raw), 2)

    def test_preserve_pinned_text_in_message_and_name(self):
        for message in ["这条消息已置顶", "已置顶的公告请查看", "公告\n已置顶"]:
            with self.subTest(message=message):
                raw = f"已置顶讨论群\n{message}\n12:00"
                self.assertEqual(
                    WindowsStateMixin._preview_from_raw(raw, "已置顶讨论群"), (message, "12:00", False)
                )

    def test_empty_preview_and_time_formats(self):
        self.assertEqual(WindowsStateMixin._preview_from_raw("测试群\n已置顶\n", "测试群"), ("", "", True))
        for time in ["12:00", "昨天 18:38", "前天 下午 3:05", "星期三", "08/19", "2026/09/05"]:
            with self.subTest(time=time):
                raw = f"测试群\n已置顶\n消息内容\n{time}\n"
                self.assertEqual(WindowsStateMixin._preview_from_raw(raw, "测试群"), ("消息内容", time, True))
