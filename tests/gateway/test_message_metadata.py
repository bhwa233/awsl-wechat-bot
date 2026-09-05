import asyncio
import json
import queue
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from common.models import GatewayMessage, ReceivedMessage, WSSubscription, parse_client_message, parse_server_message
from gateway.adapters.linux import LinuxWeChatAdapter, LinuxWeChatWindow
from gateway.core.detector import MessageDetector
from gateway.core.gateway import Gateway

with patch.dict(sys.modules, {'uiautomation': SimpleNamespace(Keys=SimpleNamespace(VK_RMENU=165))}):
    from gateway.adapters.windows import WindowsWeChatAdapter, _MessageRecord, _MessageState
    from gateway.console.adapters.windows.adapter import WindowsConsoleAdapter


class MessageMetadataTest(unittest.TestCase):
    def test_same_text_different_senders_survives_linux_detector_and_broadcast(self):
        adapter = LinuxWeChatAdapter.__new__(LinuxWeChatAdapter)
        adapter._seen_lock = threading.Lock()
        adapter._last_pull = {}
        data = [{
            'seq': 1, 'talker': 'room@chatroom', 'sender_name': nickname, 'is_self': False,
            'time': '2026-09-05T12:00:00', 'content': '同样的正文', 'msg_type': 1,
            'sender': wxid, 'at_users': ats,
        } for wxid, nickname, ats in [('wxid_a', '甲', ['wxid_b']), ('wxid_b', '乙', [])]]
        adapter._chatlog = Mock()
        adapter._chatlog.get.return_value.json.return_value = list(reversed(data))
        window = LinuxWeChatWindow('测试群', '1', 'room@chatroom')
        events = queue.Queue()
        detector = MessageDetector('测试群', window, adapter, gateway_queue=events)
        detector._check_messages()
        first, second = events.get_nowait(), events.get_nowait()
        self.assertEqual(first.sender, 'wxid_a')
        self.assertEqual(second.sender, 'wxid_b')
        self.assertEqual(first.sender_name, '甲')
        self.assertEqual(second.sender_name, '乙')
        self.assertEqual(first.at_users, ['wxid_b'])
        self.assertEqual(second.at_users, [])
        received = []

        class Client:
            async def send_text(self, payload):
                received.append(parse_server_message(payload))

        gateway = Gateway(Mock())
        gateway._loop = object()
        gateway._ws_clients[Client()] = WSSubscription(subscribed=True)
        with patch('gateway.core.gateway.asyncio.run_coroutine_threadsafe', side_effect=lambda coro, loop: asyncio.run(coro)):
            gateway._broadcast(first)
        self.assertEqual(received[0].sender, first.sender)
        self.assertEqual(received[0].sender_name, first.sender_name)
        self.assertEqual(received[0].at_users, first.at_users)

    def test_old_protocol_and_reply_to_message_round_trip(self):
        old = parse_server_message(json.dumps({'type': 'message', 'content': '正文', 'group_name': '群', 'timestamp': 1}))
        self.assertEqual(old.sender, '')
        self.assertNotIn('quote', old.model_dump())
        request = parse_client_message(json.dumps({
            'type': 'send_message', 'group_name': '群', 'message': '回复', 'reply_to_message': '原文',
        }))
        self.assertEqual(request.to_outgoing_message().reply_to_message, '原文')
        event = GatewayMessage(group_name='群', timestamp=1, **ReceivedMessage(
            content='正文', sender='wxid_a',
        ).model_dump())
        self.assertEqual(event.sender, 'wxid_a')

    def test_reply_lookup_exact_before_normalized_and_latest_duplicate(self):
        def item(text, cls='mmui::ChatTextItemView'):
            return SimpleNamespace(Name=text, ClassName=cls, ControlTypeName='ListItemControl')
        old, latest, whitespace = item('a b'), item('a b'), item('a\n b')
        self.assertIs(WindowsWeChatAdapter._find_reply_message([old, latest, whitespace], 'a b'), latest)
        self.assertIs(WindowsWeChatAdapter._find_reply_message([old, latest, whitespace], ' a   b '), whitespace)
        quote = item('回复\n引用 小明 的消息：原文')  # noqa: RUF001
        self.assertIs(WindowsWeChatAdapter._find_reply_message([quote], '回复'), quote)
        self.assertIs(WindowsWeChatAdapter._find_reply_message([quote], quote.Name), quote)
        self.assertIsNone(WindowsWeChatAdapter._find_reply_message([quote], '原文'))
        self.assertIsNone(WindowsWeChatAdapter._find_reply_message([item('回复引用 小明 的消息: 原文')], '回复'))
        self.assertIsNone(WindowsWeChatAdapter._find_reply_message([item('订单不可以退款')], '订单可以退款'))
        self.assertIsNone(WindowsWeChatAdapter._find_reply_message([old], ''))

    def test_windows_dedup_still_distinguishes_same_text_controls(self):
        adapter = WindowsWeChatAdapter()
        adapter._message_state_key = Mock(return_value='room')
        old = _MessageRecord(ReceivedMessage(content='旧消息'), (1,), 'old')
        state = _MessageState(observed=[old])
        adapter._message_states['room'] = state
        window = SimpleNamespace(Name='测试群')
        adapter.select_new_messages(window, [], initialize=True)
        first = _MessageRecord(ReceivedMessage(content='相同正文\n引用 甲 的消息: 原文'), (2,), 'first')
        second = _MessageRecord(ReceivedMessage(content='相同正文\n引用 乙 的消息: 原文'), (3,), 'second')
        state.observed = [old, first, second]
        messages = adapter.select_new_messages(window, [])
        self.assertEqual(messages, [first.message, second.message])
        self.assertEqual(adapter.select_new_messages(window, []), [])

    def test_reply_omission_and_missing_message_never_open_menu(self):
        adapter = WindowsWeChatAdapter()
        window = Mock()
        adapter.clear_reply = Mock()
        with adapter.reply_context(window, None) as quoted:
            self.assertFalse(quoted)
        window.ListControl.assert_not_called()
        window.GroupControl.assert_not_called()
        adapter._reply_preview = Mock(return_value=None)
        window.ListControl.return_value.GetChildren.return_value = []
        with adapter.reply_context(window, '不存在') as quoted:
            self.assertFalse(quoted)
        adapter.clear_reply.assert_not_called()

    def test_new_quote_draft_after_initial_cleanup_stops_reply_preparation(self):
        adapter = WindowsWeChatAdapter()
        adapter._reply_preview = Mock(return_value=object())
        adapter.clear_reply = Mock()
        with self.assertRaisesRegex(RuntimeError, '已有引用草稿'), adapter.reply_context(Mock(), '原文'):
            self.fail('已有草稿时不进入发送')
        adapter.clear_reply.assert_not_called()

    def test_text_send_continues_after_cleanup_failure_and_retries_next_time(self):
        adapter = WindowsWeChatAdapter()
        window = Mock(NativeWindowHandle=123, Name='Bruce')
        window.EditControl.return_value.Name = 'Bruce'
        target, preview = Mock(), Mock()
        button = preview.ButtonControl.return_value
        button.Exists.return_value = False
        button.IsOffscreen = False
        state = {'quote': True}
        button.Click.side_effect = lambda **kwargs: state.update(quote=False)
        adapter._ensure_uia = Mock()
        adapter.activate_specific_window = Mock(return_value=True)
        adapter._wait_for_reply = lambda predicate: predicate()
        adapter._reply_preview = lambda window: preview if state['quote'] else None
        adapter._focus_send_target = Mock(return_value=target)
        adapter._count_at_popovers = Mock(return_value=0)
        adapter._message_state_key = Mock(return_value='Bruce')
        automation = Mock()
        automation.GetClipboardText.return_value = '回复'
        with patch('gateway.adapters.windows.auto', automation):
            with self.assertLogs('gateway.adapters.windows', level='WARNING'):
                self.assertTrue(adapter.send_text_to_window(window, '回复'))
            self.assertTrue(state['quote'])
            button.Exists.return_value = True
            self.assertTrue(adapter.send_text_to_window(window, '回复'))
            self.assertFalse(state['quote'])
            self.assertTrue(adapter.send_text_to_window(window, '回复'))
        button.Click.assert_called_once()
        self.assertEqual(sum(call.args[0] == '{ENTER}' for call in target.SendKeys.call_args_list), 3)

    def test_quote_cleanup_skips_changed_chat_or_missing_preview(self):
        adapter = WindowsWeChatAdapter()
        window = Mock(NativeWindowHandle=123)
        window.EditControl.return_value.Name = '另一聊天'
        adapter._reply_preview = Mock(return_value=None)
        adapter._wait_for_reply = lambda predicate: predicate()
        adapter.clear_reply(window, (123, 'Bruce'))
        adapter._reply_preview.assert_not_called()
        window.EditControl.return_value.Name = 'Bruce'
        adapter.clear_reply(window, (123, 'Bruce'))
        adapter._reply_preview.assert_called()

    def test_console_uses_own_input_and_continues_after_quote_cleanup_error(self):
        adapter = WindowsWeChatAdapter()
        adapter._reply_preview = Mock(side_effect=RuntimeError('UIA 引用检查失败'))
        adapter._focus_send_target = Mock(side_effect=AssertionError('Console 不应调用独立窗口输入定位'))
        console = WindowsConsoleAdapter.__new__(WindowsConsoleAdapter)
        window, target = Mock(), Mock()
        console._wechat_adapter = adapter
        console._ensure_uia = Mock()
        console._find_main_window = Mock(return_value=window)
        console._ui_operation_lock = Mock(return_value=threading.Lock())
        console._activate_window_locked = Mock(return_value=True)
        console._current_chat_context = Mock(return_value=('Bruce', ['Bruce']))
        console._focus_input = Mock(return_value=target)
        console._count_at_popovers = Mock(return_value=0)
        console._set_clipboard_text_verified = Mock()
        console.get_state = Mock(return_value={'available': True})
        with self.assertLogs('gateway.adapters.windows', level='WARNING'), patch('time.sleep'):
            for _ in range(2):
                self.assertTrue(console._send_text_message(text='回复', chat_name='Bruce')['available'])
        self.assertEqual(console._focus_input.call_count, 2)
        adapter._focus_send_target.assert_not_called()
        self.assertEqual(sum(call.args[0] == '{ENTER}' for call in target.SendKeys.call_args_list), 2)

    def test_end_cleanup_failure_is_reported_without_raising(self):
        adapter = WindowsWeChatAdapter()
        window = Mock(NativeWindowHandle=123)
        window.EditControl.return_value.Name = 'Bruce'
        preview = Mock()
        preview.ButtonControl.return_value.Exists.return_value = False
        adapter._reply_preview = Mock(return_value=preview)
        adapter._wait_for_reply = lambda predicate: predicate()
        with self.assertLogs('gateway.adapters.windows', level='WARNING'):
            self.assertFalse(adapter.clear_reply(window, (123, 'Bruce')))

    def test_quote_cleanup_uses_observed_button_name(self):
        adapter = WindowsWeChatAdapter()
        window = Mock(NativeWindowHandle=123)
        window.EditControl.return_value.Name = 'Bruce'
        adapter._wait_for_reply = lambda predicate: predicate()
        preview = Mock()
        preview.ButtonControl.return_value.IsOffscreen = False
        adapter._reply_preview = Mock(side_effect=[preview, preview, None])
        adapter.activate_specific_window = Mock(return_value=True)
        adapter.clear_reply(window, (123, 'Bruce'))
        preview.ButtonControl.assert_called_once_with(searchDepth=2, Name='删除引用消息')
        preview.ButtonControl.return_value.Click.assert_called_once()

    def test_reply_menu_is_a_child_of_the_chat_window(self):
        menu = SimpleNamespace(ClassName='mmui::XMenu', IsOffscreen=False)
        hidden = SimpleNamespace(ClassName='mmui::XMenu', IsOffscreen=True)
        window = Mock()
        window.GetChildren.return_value = [menu, hidden]
        self.assertEqual(WindowsWeChatAdapter._reply_menus(window), [menu])

    def test_reply_tries_both_bubble_sides_with_dpi_scaling(self):
        for dpi, outcome in [(96, 'ok'), (144, 'ok'), (96, 'send_error'), (96, 'timeout')]:
            with self.subTest(dpi=dpi, outcome=outcome):
                adapter = WindowsWeChatAdapter()
                adapter._wait_for_reply = lambda predicate: predicate()
                state = {'menu': False, 'quote': False}
                rect = SimpleNamespace(left=100, top=200, right=1200, bottom=400)
                item = Mock(Name='原文', ClassName='mmui::ChatTextItemView', BoundingRectangle=rect)
                item.GetRuntimeId.return_value = (1, 2)
                item.IsOffscreen = False
                window = Mock(NativeWindowHandle=123)
                window.EditControl.return_value.Name = 'Bruce'
                window.ListControl.return_value.GetChildren.return_value = [item]
                window.ListControl.return_value.BoundingRectangle = rect
                option = Mock(Name='引用', ControlTypeName='MenuItemControl', IsEnabled=True)
                option.Click.side_effect = lambda state=state, outcome=outcome, **kwargs: state.update(
                    menu=False, quote=outcome != 'timeout',
                )
                menu = Mock()
                menu.GetChildren.return_value = [option]
                adapter._reply_menus = lambda window, menu=menu, state=state: [menu] if state['menu'] else []
                adapter._reply_preview = lambda window, state=state: object() if state['quote'] else None
                left = round(rect.left + 90 * dpi / 96)
                right = round(rect.right - 90 * dpi / 96)
                y = round(rect.top + 28 * dpi / 96)
                automation = Mock()
                automation.RightClick.side_effect = lambda x, y, state=state, left=left, **kwargs: state.update(menu=x == left)
                user32 = SimpleNamespace(GetDpiForWindow=lambda handle, dpi=dpi: dpi, GetForegroundWindow=lambda: 123)
                adapter.clear_reply = Mock()
                with patch('gateway.adapters.windows.auto', automation), patch(
                    'ctypes.windll', SimpleNamespace(user32=user32), create=True,
                ):
                    if outcome == 'timeout':
                        with self.assertRaisesRegex(RuntimeError, '未确认引用预览'), adapter.reply_context(window, '原文'):
                            self.fail('引用未确认时不进入发送')
                    elif outcome == 'send_error':
                        with self.assertRaisesRegex(RuntimeError, '发送异常'), adapter.reply_context(window, '原文') as quoted:
                            self.assertTrue(quoted)
                            raise RuntimeError('发送异常')
                    else:
                        with adapter.reply_context(window, '原文') as quoted:
                            self.assertTrue(quoted)
                            adapter.clear_reply.assert_not_called()
                self.assertEqual([call.args for call in automation.RightClick.call_args_list], [(right, y), (left, y)])
                adapter.clear_reply.assert_called_once_with(window, (123, 'Bruce'))
