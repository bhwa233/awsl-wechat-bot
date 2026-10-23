import json
import sys
import threading
import unittest
from unittest.mock import Mock, patch

import httpx

if sys.platform == "win32":
    raise unittest.SkipTest("Linux adapter requires POSIX")

from gateway.adapters.linux import LinuxWeChatAdapter, LinuxWeChatWindow
from gateway.console.adapters.linux import LinuxConsoleAdapter
from gateway.utils.linux_accessibility import AccessibleRef


def message(seq, content, *, self_sent=False, server_id=None, msg_type=1):
    return {"seq": seq, "time": "2026-10-22T12:00:00", "content": content, "is_self": self_sent,
            "server_id": seq if server_id is None else server_id, "local_id": seq, "msg_type": msg_type}


class LinuxMessageCursorTest(unittest.TestCase):
    def setUp(self):
        self.adapter = object.__new__(LinuxWeChatAdapter)
        self.adapter._seen_lock = threading.Lock()
        self.adapter._last_seq = {"group": 100}
        self.adapter._seen_messages = {"group": {(100, 100, 100)}}
        self.rows = []

        def request(req):
            payload = json.loads(req.content)
            self.assertEqual(req.url.path, "/api/chatlog/v2/messages/batch")
            cursor = payload["items"][0]["since_seq"]
            offset = payload.get("offset", 0)
            rows = [row for row in self.rows if row["seq"] >= cursor][offset:offset + payload["limit"]]
            return httpx.Response(200, json={"results": {"group": rows}})

        self.adapter._chatlog = httpx.Client(base_url="http://chatlog", transport=httpx.MockTransport(request))
        self.window = LinuxWeChatWindow("group", "0x1", "group")

    def tearDown(self):
        self.adapter._chatlog.close()

    def test_late_message_in_same_second_is_not_lost(self):
        self.rows = [message(100, "old"), message(101, "late")]
        self.assertEqual([m.content for m in self.adapter.get_messages_from_window(self.window)], ["late"])
        self.assertEqual(self.adapter.get_messages_from_window(self.window), [])

    def test_backlog_is_drained_in_order(self):
        self.rows = [message(seq, str(seq)) for seq in range(100, 108)]
        with patch("gateway.adapters.linux.config.LINUX_CHATLOG_MESSAGE_LIMIT", 3):
            result = [m.content for _ in range(4) for m in self.adapter.get_messages_from_window(self.window)]
        self.assertEqual(result, [str(seq) for seq in range(101, 108)])

    def test_pending_self_message_does_not_block_peer_backlog(self):
        self.rows = [message(101, "pending", self_sent=True, server_id=0)]
        self.rows += [message(seq, str(seq)) for seq in range(102, 110)]
        with patch("gateway.adapters.linux.config.LINUX_CHATLOG_MESSAGE_LIMIT", 3):
            result = [m.content for _ in range(8) for m in self.adapter.get_messages_from_window(self.window)]
        self.assertEqual(result, [str(seq) for seq in range(102, 110)])
        self.assertEqual(self.adapter._last_seq["group"], 109)

    def test_late_self_ack_is_filtered_without_replaying_peer(self):
        self.rows = [message(101, "pending", self_sent=True, server_id=0), message(102, "peer")]
        self.assertEqual([m.content for m in self.adapter.get_messages_from_window(self.window)], ["peer"])
        self.assertEqual(self.adapter._last_seq["group"], 102)
        self.assertEqual(self.adapter.get_messages_from_window(self.window), [])
        self.rows[0]["server_id"] = 999
        self.assertEqual(self.adapter.get_messages_from_window(self.window), [])
        self.assertEqual(self.adapter._last_seq["group"], 102)

    def test_non_text_messages_advance_cursor(self):
        self.rows = [message(101, "image", msg_type=3), message(102, "self", self_sent=True)]
        self.assertEqual(self.adapter.get_messages_from_window(self.window), [])
        self.assertEqual(self.adapter._last_seq["group"], 102)

    def test_same_seq_backlog_crosses_multiple_pages(self):
        self.rows = [message(101, str(i), server_id=1000+i) for i in range(8)] + [message(102, "next")]
        with patch("gateway.adapters.linux.config.LINUX_CHATLOG_MESSAGE_LIMIT", 3):
            result = [m.content for _ in range(4) for m in self.adapter.get_messages_from_window(self.window)]
        self.assertEqual(result, [str(i) for i in range(8)] + ["next"])
        self.assertEqual(self.adapter._last_seq["group"], 102)

    def test_late_same_seq_message_is_read_after_full_boundary(self):
        self.rows = [message(101, str(i), server_id=1000+i) for i in range(6)]
        with patch("gateway.adapters.linux.config.LINUX_CHATLOG_MESSAGE_LIMIT", 3):
            self.adapter.get_messages_from_window(self.window)
            self.adapter.get_messages_from_window(self.window)
            self.rows.append(message(101, "late", server_id=9999))
            self.assertEqual([m.content for m in self.adapter.get_messages_from_window(self.window)], ["late"])

    def test_failed_batch_keeps_cursor_until_full_retry_succeeds(self):
        self.adapter._chatlog.close()
        responses = [httpx.Response(500), httpx.Response(200, json={"results": {"group": [message(101, "older"), message(102, "newer")]}})]
        self.adapter._chatlog = httpx.Client(base_url="http://chatlog", transport=httpx.MockTransport(lambda req: responses.pop(0)))
        with self.assertRaises(RuntimeError):
            self.adapter.get_messages_from_window(self.window)
        self.assertEqual(self.adapter._last_seq["group"], 100)
        self.assertEqual([m.content for m in self.adapter.get_messages_from_window(self.window)], ["older", "newer"])


class LinuxSendGuardTest(unittest.TestCase):
    def test_current_chat_is_focused_without_reclicking_session(self):
        adapter = Mock()
        adapter._ambiguous_titles = set()
        adapter.window_nodes.return_value = [self.field()]
        adapter._focus_input_area.return_value = True
        console = LinuxConsoleAdapter(adapter)
        console._click = Mock()
        window = LinuxWeChatWindow("WeChat", "0x1")
        self.assertEqual(console._select(window, {"name": "group\npreview", "selected": True}), "group")
        self.assertEqual(window.expected_chat, "group")
        console._click.assert_not_called()

    def test_same_name_session_is_rejected_before_click_or_focus(self):
        adapter = Mock()
        adapter._ambiguous_titles = {"group"}
        adapter.window_nodes.return_value = [self.field()]
        console = LinuxConsoleAdapter(adapter)
        console._click = Mock()
        with self.assertRaisesRegex(RuntimeError, "同名会话"):
            console._select(LinuxWeChatWindow("WeChat", "0x1"), {"name": "group", "selected": False})
        console._click.assert_not_called()
        adapter._focus_input_area.assert_not_called()

    def test_unselected_session_with_same_title_is_clicked(self):
        adapter = Mock()
        adapter._ambiguous_titles = set()
        adapter.window_nodes.return_value = [self.field()]
        adapter._focus_input_area.return_value = True
        console = LinuxConsoleAdapter(adapter)
        console._click = Mock()
        node = {"name": "group", "selected": False, "ref": "session"}
        adapter.accessibility.info.return_value = node
        console._select(LinuxWeChatWindow("WeChat", "0x1"), node)
        console._click.assert_called_once_with(node)

    def test_selection_accepts_changed_preview_but_rejects_changed_chat(self):
        adapter = Mock()
        adapter._ambiguous_titles = set()
        adapter.window_nodes.return_value = [self.field()]
        adapter._focus_input_area.return_value = True
        console = LinuxConsoleAdapter(adapter)
        console._click = Mock()
        old = {"name": "group [1]\nold preview", "selected": False, "ref": "session"}
        fresh = {**old, "name": "group\nnew preview"}
        adapter.accessibility.info.return_value = fresh
        console._select(LinuxWeChatWindow("WeChat", "0x1"), old)
        console._click.assert_called_once_with(fresh)
        console._click.reset_mock()
        adapter.accessibility.info.return_value = {**fresh, "name": "another group"}
        with self.assertRaisesRegex(RuntimeError, "会话已变化"):
            console._select(LinuxWeChatWindow("WeChat", "0x1"), old)
        console._click.assert_not_called()

    def test_already_open_window_restores_main_selection(self):
        adapter = Mock()
        adapter._ambiguous_titles = set()
        adapter._title_to_talker = {"group": "group@chatroom"}
        adapter.window_nodes.return_value = []
        console = LinuxConsoleAdapter(adapter)
        console._windows = Mock(return_value=[LinuxWeChatWindow("group", "0x2", "group@chatroom")])
        console._select = Mock()
        main = LinuxWeChatWindow("WeChat", "0x1")
        node = {"name": "group"}
        self.assertEqual(console._open_standalone(main, node)["status"], "already_open")
        console._select.assert_called_once_with(main, node)

    def test_open_standalone_rejects_ambiguous_name_without_id(self):
        adapter = Mock()
        adapter._ambiguous_titles = {"group"}
        adapter._title_to_talker = {}
        adapter.window_nodes.return_value = []
        console = LinuxConsoleAdapter(adapter)
        console._click = Mock()
        with self.assertRaisesRegex(RuntimeError, "同名会话"):
            console._open_standalone(LinuxWeChatWindow("WeChat", "0x1"), {"name": "group"})
        console._click.assert_not_called()

    @staticmethod
    def field(**changes):
        return {"role": "text", "name": "group", "enabled": True, "focused": True,
                "rect": {"left": 10, "top": 100, "width": 100, "height": 100, "bottom": 200}, **changes}

    def test_standalone_requires_focused_input(self):
        adapter = object.__new__(LinuxWeChatAdapter)
        adapter._ambiguous_titles = set()
        adapter._active_window_matches = Mock(return_value=True)
        adapter.window_nodes = Mock(return_value=[self.field(focused=False)])
        self.assertFalse(adapter._send_context_matches(LinuxWeChatWindow("group", "0x1")))
        adapter.window_nodes.return_value = [self.field()]
        self.assertTrue(adapter._send_context_matches(LinuxWeChatWindow("group", "0x1")))

    def test_standalone_accessibility_failure_does_not_click_geometry(self):
        adapter = object.__new__(LinuxWeChatAdapter)
        adapter._ambiguous_titles = set()
        adapter._active_window_matches = Mock(return_value=True)
        adapter.window_nodes = Mock(side_effect=RuntimeError("unavailable"))
        adapter._run_x11 = Mock()
        self.assertFalse(adapter._focus_input_area(LinuxWeChatWindow("group", "0x1")))
        adapter._run_x11.assert_not_called()

    def test_zero_sized_input_is_rejected(self):
        adapter = object.__new__(LinuxWeChatAdapter)
        adapter._ambiguous_titles = set()
        adapter._active_window_matches = Mock(return_value=True)
        adapter.window_nodes = Mock(return_value=[self.field(rect={"left": 0, "top": 0, "width": 0, "height": 0})])
        self.assertFalse(adapter._send_context_matches(LinuxWeChatWindow("group", "0x1")))

    def test_focus_stolen_after_paste_never_sends_return(self):
        adapter = object.__new__(LinuxWeChatAdapter)
        adapter._send_context_matches = Mock(side_effect=[True, False])
        adapter._keys = Mock(return_value=True)
        adapter._run_x11 = Mock()
        with patch("gateway.adapters.linux.time.sleep"):
            self.assertFalse(adapter._paste_and_send(LinuxWeChatWindow("group", "0x1")))
        adapter._run_x11.assert_not_called()

    def test_voice_send_button_rejects_stolen_focus(self):
        adapter = object.__new__(LinuxWeChatAdapter)
        adapter._active_window_matches = Mock(return_value=False)
        adapter.window_nodes = Mock()
        adapter.accessibility = Mock()
        self.assertFalse(adapter._voice_button(LinuxWeChatWindow("group", "0x1"), {"Send voice message"}))
        adapter.window_nodes.assert_not_called()
        adapter.accessibility.call.assert_not_called()

    def test_background_voice_cancel_rejects_another_chat(self):
        adapter = object.__new__(LinuxWeChatAdapter)
        adapter.window_nodes = Mock(return_value=[
            self.field(name="other chat"),
        ])
        adapter.accessibility = Mock()
        window = LinuxWeChatWindow("WeChat", "0x1", expected_chat="target chat")
        self.assertFalse(adapter._voice_button(window, {"Cancel"}, require_active=False))
        adapter.accessibility.call.assert_not_called()

    def test_stale_session_path_is_rejected(self):
        adapter = Mock()
        console = LinuxConsoleAdapter(adapter)
        old = {"ref": AccessibleRef(":1.0", "/session/1"), "name": "old\npreview"}
        token = console._remember(old)
        adapter.window_nodes.return_value = [
            {"role": "list", "name": "Chats", "depth": 0, "path": "0"},
            {"role": "list item", "name": "other\npreview", "ref": old["ref"], "depth": 1, "path": "0.0"},
        ]
        with self.assertRaisesRegex(RuntimeError, "会话列表已变化"):
            console._session(Mock(), token)
        adapter._run_x11.assert_not_called()

    def test_message_preview_change_does_not_invalidate_session(self):
        adapter = Mock()
        console = LinuxConsoleAdapter(adapter)
        old = {"ref": AccessibleRef(":1.0", "/session/1"), "name": "group\nold preview"}
        token = console._remember(old)
        fresh = {"role": "list item", "name": "group\nnew preview", "ref": old["ref"], "depth": 1, "path": "0.0"}
        adapter.window_nodes.return_value = [
            {"role": "list", "name": "Chats", "depth": 0, "path": "0"}, fresh,
        ]
        self.assertEqual(console._remember(fresh), token)
        self.assertIs(console._session(Mock(), token), fresh)

    def test_quote_cleanup_never_clicks_in_another_chat(self):
        adapter = Mock()
        adapter.window_nodes.return_value = [
            self.field(name="other chat"),
        ]
        console = LinuxConsoleAdapter(adapter)
        window = LinuxWeChatWindow("WeChat", "0x1", expected_chat="target chat")
        with self.assertRaisesRegex(RuntimeError, "会话已变化"):
            console._clear_reply(window)
        adapter._run_x11.assert_not_called()

    def test_quote_verifies_copy_and_does_not_escape_without_menu(self):
        adapter = Mock()
        adapter._active_window_matches.return_value = True
        node = {"name": "body", "ref": "message", "rect": {"left": 100, "top": 200, "width": 1000, "height": 50}}
        adapter.accessibility.info.return_value = node
        adapter._run_x11.return_value = Mock(returncode=0, stdout="body")
        adapter._set_clipboard_text.return_value = True
        console = LinuxConsoleAdapter(adapter)
        copy = {"name": "Copy", "ref": "copy"}
        console._menu_nodes = Mock(side_effect=[[], [copy, {"name": "Quote"}]])
        console._click = Mock()
        console._menu = Mock()
        with patch("gateway.console.adapters.linux.time.sleep"):
            console._quote_message(LinuxWeChatWindow("WeChat", "0x1"), node, "body")
        adapter._keys.assert_not_called()
        console._click.assert_called_once_with(copy)
        console._menu.assert_called_once_with({"引用", "Reply", "Quote"})

    def test_quote_rejects_clipboard_body_mismatch(self):
        adapter = Mock()
        node = {"name": "body", "ref": "message", "rect": {"left": 100, "top": 200, "width": 1000, "height": 50}}
        adapter.accessibility.info.return_value = node
        adapter._run_x11.return_value = Mock(returncode=0, stdout="different body")
        console = LinuxConsoleAdapter(adapter)
        console._menu_nodes = Mock(return_value=[{"name": "Copy"}, {"name": "Quote"}])
        console._click = Mock()
        console._menu = Mock()
        with patch("gateway.console.adapters.linux.time.sleep"), self.assertRaisesRegex(RuntimeError, "正文不一致"):
            console._quote_message(LinuxWeChatWindow("WeChat", "0x1"), node, "body")
        console._menu.assert_not_called()


class LinuxMentionTest(unittest.TestCase):
    def setUp(self):
        self.adapter = object.__new__(LinuxWeChatAdapter)
        self.window = LinuxWeChatWindow("group", "0x1")
        self.adapter._send_context_matches = Mock(return_value=True)
        self.adapter._active_window_matches = Mock(return_value=True)
        self.adapter.window_nodes = Mock(return_value=[LinuxSendGuardTest.field(ref="input")])
        self.adapter.accessibility = Mock()
        self.adapter.accessibility.text.side_effect = ["", "\ufffc"]
        self.adapter._mention_popups = Mock(side_effect=[[], [], [{"ref": "popup"}], []])
        self.candidate = {"ref": "candidate", "role": "list item", "name": "George",
                          "rect": {"left": 0, "top": 0, "width": 100, "height": 30}}
        self.adapter.accessibility.nodes.return_value = [self.candidate]
        self.adapter.accessibility.info.return_value = self.candidate
        self.adapter._run_x11 = Mock(return_value=Mock(returncode=0))
        self.adapter._set_clipboard_text = Mock(return_value=True)
        self.adapter._stop_clipboard_owner = Mock()
        self.adapter._keys = Mock(return_value=True)

    def test_waits_for_popup_and_confirms_native_chip(self):
        def clipboard(name):
            self.assertEqual(self.adapter._mention_popups.call_count, 3)
            return True
        self.adapter._set_clipboard_text.side_effect = clipboard
        with patch("gateway.adapters.linux.time.sleep"):
            self.adapter._select_mention(self.window, "George")
        self.adapter.accessibility.nodes.assert_called_once_with("popup", max_nodes=200, max_depth=12)
        self.assertEqual(self.adapter.accessibility.text.call_count, 2)

    def test_duplicate_candidates_are_not_clicked(self):
        self.adapter.accessibility.nodes.return_value = [self.candidate, self.candidate]
        with patch("gateway.adapters.linux.time.sleep"), self.assertRaisesRegex(RuntimeError, "重名"):
            self.adapter._select_mention(self.window, "George")
        self.assertEqual(self.adapter._run_x11.call_count, 1)

    def test_popup_closing_without_chip_is_not_success(self):
        self.adapter.accessibility.text.side_effect = None
        self.adapter.accessibility.text.return_value = "@George"
        self.adapter._mention_popups.side_effect = [[], [{"ref": "popup"}], [], []]
        with patch("gateway.adapters.linux.time.monotonic", side_effect=[0, 0, 0, 0, 0, 0, 4]), \
                patch("gateway.adapters.linux.time.sleep"), \
                self.assertRaisesRegex(RuntimeError, "未能确认"):
            self.adapter._select_mention(self.window, "George")


if __name__ == "__main__":
    unittest.main()
