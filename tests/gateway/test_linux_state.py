import sys
import unittest
from unittest.mock import Mock

if sys.platform == "win32":
    raise unittest.SkipTest("Linux adapter requires POSIX")

from gateway.adapters.linux import LinuxWeChatWindow
from gateway.console.adapters.linux import LinuxConsoleAdapter
from gateway.utils.linux_accessibility import ACCESSIBLE, AccessibleRef, LinuxAccessibility


class LinuxConsoleStateTest(unittest.TestCase):
    def test_chinese_and_english_quote_preserve_sender_and_multiline_body(self):
        for raw in ["reply\n引用 Misaka 的消息: original\nsecond line",
                    "reply\nQuote Misaka's message: original\nsecond line"]:
            self.assertEqual(LinuxConsoleAdapter._message_content_and_quote(raw),
                             ("reply", {"sender": "Misaka", "content": "original\nsecond line"}))
        raw = "a sentence with Quote Misaka's message: ordinary text"
        self.assertEqual(LinuxConsoleAdapter._message_content_and_quote(raw), (raw, None))

    def test_timestamp_geometry_distinguishes_time_looking_message_bubble(self):
        console = LinuxConsoleAdapter(Mock())
        parent = {"path": "0", "depth": 0}
        rows = [{"path": f"0.{i}", "depth": 1, "role": "list item", "name": name,
                 "rect": {"height": height}} for i, (name, height) in enumerate([
                     ("12:34", 41), ("12:34", 55), ("normal message", 55),
                     ("reply\nQuote Misaka's message: original", 80)])]
        items = console._message_items(rows, parent)
        self.assertEqual([item["kind"] for item in items], ["timestamp", "text", "text", "text"])
        self.assertEqual(items[-1]["quote"], {"sender": "Misaka", "content": "original"})
        self.assertEqual(items[-1]["content"], "reply")
        self.assertEqual(items[-1]["raw_text"], rows[-1]["name"])

    def test_time_formats_and_non_time_text(self):
        for value in ["12:34", "Yesterday 12:34", "昨天 下午 3:05", "星期三", "Thursday", "2026/10/22", "2026年10月22日 12:34"]:
            self.assertTrue(LinuxConsoleAdapter._looks_like_time(value), value)
        for value in ["Audio7\"sec", "2", "time 12:34", "12:34\nmessage"]:
            self.assertFalse(LinuxConsoleAdapter._looks_like_time(value), value)

    def test_pinned_preview_time_and_mute_match_state_fields(self):
        adapter = Mock()
        nodes = [
            {"path": "0", "depth": 0, "role": "list", "name": "Chats", "rect": {}},
            {"path": "0.0", "depth": 1, "role": "list item", "ref": AccessibleRef(":1.0", "/session"),
             "name": "group [2]\nPinned\npreview\nsecond line\n15:30\nMute Notifications\n", "rect": {}},
        ]
        adapter.window_nodes.return_value = nodes
        console = LinuxConsoleAdapter(adapter)
        console._window = Mock(return_value=LinuxWeChatWindow("WeChat", "0x1"))
        item = console.get_state()["chat_list"]["items"][0]
        self.assertEqual(item["name"], "group")
        self.assertEqual(item["preview"], "preview\nsecond line")
        self.assertEqual(item["time"], "15:30")
        self.assertEqual(item["unread"], 2)
        self.assertTrue(item["muted"])
        self.assertTrue(item["pinned"])

    def test_debug_patterns_are_optional_and_scoped_to_requested_subtree(self):
        adapter = Mock()
        adapter.window_nodes.return_value = [
            {"path": "", "depth": 0, "ref": "root"},
            {"path": "0", "depth": 1, "ref": "child"},
            {"path": "1", "depth": 1, "ref": "other"},
        ]
        details = {"extra": {"interfaces": ["org.a11y.atspi.Text"]}, "patterns": {"text": {"text": "body"}}}
        adapter.accessibility.debug_info.return_value = details
        console = LinuxConsoleAdapter(adapter)
        console._window = Mock(return_value=LinuxWeChatWindow("WeChat", "0x1"))
        plain = console.dump_window(handle=1, path="0")
        self.assertTrue(plain["available"])
        self.assertNotIn("patterns", plain["tree"])
        adapter.accessibility.debug_info.assert_not_called()
        detailed = console.dump_window(handle=1, path="0", include_patterns=True)
        self.assertEqual(detailed["tree"]["patterns"], details["patterns"])
        adapter.accessibility.debug_info.assert_called_once_with("child")

    def test_accessibility_debug_reads_capabilities_without_invoking_actions(self):
        accessibility = LinuxAccessibility()
        interfaces = [ACCESSIBLE, "org.a11y.atspi.Component", "org.a11y.atspi.Action", "org.a11y.atspi.Text"]
        accessibility.call = Mock(side_effect=[(interfaces,), ({"class": "native"},),
                                              ([("click", "activate", "")],), ("body",)])
        result = accessibility.debug_info(AccessibleRef(":1.0", "/message"))
        self.assertEqual(result["patterns"]["text"]["text"], "body")
        self.assertEqual(result["patterns"]["action"]["actions"][0]["name"], "click")
        self.assertEqual(result["extra"]["attributes"], {"class": "native"})
        self.assertEqual([call.args[2] for call in accessibility.call.call_args_list],
                         ["GetInterfaces", "GetAttributes", "GetActions", "GetText"])
