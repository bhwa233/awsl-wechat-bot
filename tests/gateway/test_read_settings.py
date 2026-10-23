import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from pydantic import ValidationError

from common.config import Config, config
from common.models import ReceivedMessage
from gateway.adapters.linux import LinuxAccessibilityReader, LinuxSentMessages, LinuxWeChatAdapter, LinuxWeChatWindow
from gateway.utils.linux_accessibility import AccessibleRef


class ReadSettingsTest(unittest.TestCase):
    def test_defaults_and_valid_choices(self):
        self.assertEqual(Config.model_fields["LINUX_MESSAGE_READER"].default, "chatlog")
        self.assertEqual(Config.model_fields["WINDOWS_MESSAGE_READER"].default, "accessibility")
        values = Config(_env_file=None, LINUX_MESSAGE_READER="accessibility", WINDOWS_MESSAGE_READER="chatlog")
        self.assertEqual((values.LINUX_MESSAGE_READER, values.WINDOWS_MESSAGE_READER), ("accessibility", "chatlog"))
        for field in ("LINUX_MESSAGE_READER", "WINDOWS_MESSAGE_READER"):
            with self.assertRaises(ValidationError):
                Config(_env_file=None, **{field: "unknown"})

    def test_linux_uses_selected_reader(self):
        adapter = LinuxWeChatAdapter.__new__(LinuxWeChatAdapter)
        adapter._atspi_readers = {}
        window = LinuxWeChatWindow("测试群", "0x1")
        with patch.object(config, "LINUX_MESSAGE_READER", "accessibility"), patch(
            "gateway.adapters.linux.LinuxAccessibilityReader"
        ) as reader_type:
            reader_type.return_value.poll.return_value = [ReceivedMessage(content="new")]
            self.assertEqual(adapter.get_messages_from_window(window)[0].content, "new")
            adapter.get_messages_from_window(window)
            reader_type.assert_called_once_with(adapter, window)


def row(index, name, height=45):
    return {'ref': AccessibleRef(':1.1', f'/row/{index}'), 'name': name, 'role': 'list item',
            'path': f'0.{index}', 'depth': 1, 'rect': {'width': 500, 'height': height}}


class AtspiReadTest(unittest.TestCase):
    def setUp(self):
        self.nodes = [{'name': 'Messages', 'role': 'list', 'path': '0', 'depth': 0}]
        self.adapter = SimpleNamespace(window_nodes=Mock(side_effect=lambda _: self.nodes), _sent_messages=LinuxSentMessages())
        self.reader = LinuxAccessibilityReader(self.adapter, SimpleNamespace(title='测试群', expected_chat=None))
        self.title = self.enterContext(patch('gateway.adapters.linux.chat_title', return_value='测试群'))

    def test_window_scan_does_not_require_chatlog_in_accessibility_mode(self):
        adapter = LinuxWeChatAdapter.__new__(LinuxWeChatAdapter)
        adapter._refresh_group_cache = Mock(side_effect=AssertionError("must not query Chatlog"))
        adapter.list_windows = Mock(return_value=[LinuxWeChatWindow('测试群', '0x1')])
        with patch.object(config, 'LINUX_MESSAGE_READER', 'accessibility'):
            self.assertEqual(adapter.find_all_wechat_windows()[0].title, '测试群')
        adapter._refresh_group_cache.assert_not_called()

    def test_append_same_text_distinct_rows_and_missing_author(self):
        self.nodes += [row(0, 'old')]
        self.assertEqual(self.reader.poll(), [])
        self.nodes += [row(1, 'same'), row(2, 'same')]
        messages = self.reader.poll()
        self.assertEqual([m.content for m in messages], ['same', 'same'])
        self.assertEqual(messages[0].sender, '')
        self.assertEqual(messages[0].sender_name, '')
        self.assertEqual(self.reader.poll(), [])

    def test_first_message_in_empty_chat(self):
        self.reader.poll()
        self.nodes += [row(0, 'first')]
        self.assertEqual(self.reader.poll()[0].content, 'first')

    def test_time_text_not_confused_with_small_timestamp_row(self):
        self.nodes += [row(0, 'old')]
        self.reader.poll()
        self.nodes += [row(1, '12:30', height=20), row(2, '12:31'), row(3, '新消息'), row(4, 'Audio9"sec')]
        self.assertEqual([m.content for m in self.reader.poll()], ['12:31', '新消息'])

    def test_scroll_and_temporary_empty_list_do_not_replay_history(self):
        self.nodes += [row(1, 'old'), row(2, 'last')]
        self.reader.poll()
        self.nodes[1:] = []
        self.assertEqual(self.reader.poll(), [])
        self.nodes += [row(1, 'old'), row(2, 'last')]
        self.assertEqual(self.reader.poll(), [])
        self.nodes[1:] = [row(0, 'history')]
        self.assertEqual(self.reader.poll(), [])

    def test_failed_read_does_not_advance_baseline(self):
        self.nodes += [row(0, 'old')]
        self.reader.poll()
        self.nodes[0]['name'] = 'unavailable'
        with self.assertRaises(RuntimeError):
            self.reader.poll()
        self.nodes[0]['name'] = 'Messages'
        self.nodes += [row(1, 'new')]
        self.assertEqual(self.reader.poll()[0].content, 'new')

    def test_gateway_self_sent_text_and_quote_filtered_per_chat(self):
        self.nodes += [row(0, 'old')]
        self.reader.poll()
        self.adapter._sent_messages.record('测试群', 'outgoing')
        self.nodes += [row(1, "outgoing\nQuote peer's message: quoted"), row(2, 'outgoing'), row(3, 'incoming')]
        self.assertEqual([m.content for m in self.reader.poll()], ['outgoing', 'incoming'])
        self.assertEqual(self.reader.poll(), [])

    def test_chat_change_refuses_read(self):
        self.title.return_value = '另一个群'
        with self.assertRaises(RuntimeError):
            self.reader.poll()
