import contextlib
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

if sys.platform == "win32":
    raise unittest.SkipTest("Linux adapter requires POSIX")

from gateway.adapters.linux import LinuxWeChatAdapter, LinuxWeChatWindow
from gateway.console.adapters.linux import LinuxConsoleAdapter
from gateway.utils import linux_voice


class LinuxAttachmentCleanupTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.adapter = object.__new__(LinuxWeChatAdapter)
        self.adapter._send_lock = threading.RLock()
        self.adapter._schedule_temp_cleanup = Mock(side_effect=self.adapter._cleanup_temp_path)
        self.window = LinuxWeChatWindow("group", "0x1", "group@chatroom")

    def send(self, kind):
        if kind == "image":
            return self.adapter.send_image_to_window(self.window, "cGF5bG9hZA==")
        return self.adapter.send_file_to_window(self.window, "cGF5bG9hZA==", "attachment.bin")

    def test_recording_guard_rejection_cleans_attachments(self):
        marker = self.directory / "pending.json"
        self.adapter._recording_marker_path = Mock(return_value=marker)
        self.adapter._finish_recording = Mock(return_value=False)
        self.adapter._activate_window = Mock()
        self.adapter._mark_recording(self.window, "retained_mic")
        for kind in ["image", "file"]:
            with self.subTest(kind=kind), patch("tempfile.tempdir", str(self.directory)), \
                    patch.object(self.adapter, "_TEMP_CLEANUP_DELAY", 0):
                self.adapter._schedule_temp_cleanup.reset_mock()
                with self.assertRaisesRegex(RuntimeError, "暂停 UI 操作"):
                    self.send(kind)
                self.adapter._schedule_temp_cleanup.assert_called_once()
                self.adapter._activate_window.assert_not_called()
                self.assertEqual(list(self.directory.iterdir()), [marker])

    def test_failed_send_cleans_attachments(self):
        self.adapter._send_file_path = Mock(return_value=False)
        for kind in ["image", "file"]:
            with self.subTest(kind=kind), patch("tempfile.tempdir", str(self.directory)), \
                    patch.object(self.adapter, "_TEMP_CLEANUP_DELAY", 0):
                self.adapter._schedule_temp_cleanup.reset_mock()
                self.assertFalse(self.send(kind))
                self.adapter._schedule_temp_cleanup.assert_called_once()
                self.assertEqual(list(self.directory.iterdir()), [])

    def test_success_keeps_existing_cleanup_schedule(self):
        self.adapter._send_file_path = Mock(return_value=True)
        for kind in ["image", "file"]:
            with self.subTest(kind=kind), patch("tempfile.tempdir", str(self.directory)):
                self.assertTrue(self.send(kind))
                self.adapter._schedule_temp_cleanup.assert_not_called()

    def test_unicode_filename_fits_linux_byte_limit_and_keeps_extension(self):
        for name in ["测" * 100 + ".txt", "测" * 300 + ".txt", "a" * 300 + ".txt"]:
            with self.subTest(name=name), patch("tempfile.tempdir", str(self.directory)):
                path = Path(self.adapter._write_temp_file(b"payload", suffix="", filename=name))
            self.assertLessEqual(len(path.name.encode("utf-8")), 200)
            self.assertEqual(path.suffix, ".txt")
            self.assertEqual(path.read_bytes(), b"payload")
        for name in [".", "..", "", " / "]:
            self.assertEqual(self.adapter._safe_filename(name), "attachment.bin")
        self.assertNotEqual(self.adapter._safe_filename("测" * 300 + "a.txt"), self.adapter._safe_filename("测" * 300 + "b.txt"))

    def test_partial_write_failure_removes_file_and_directory(self):
        real_open, real_fdopen = open, os.fdopen

        @contextlib.contextmanager
        def failed_write(*args, opener, **kwargs):
            with opener(*args, **kwargs) as file:
                def write(data):
                    file.write(data[:2])
                    raise OSError("disk full")
                yield Mock(write=write)

        for filename in [None, "attachment.txt"]:
            target = "builtins.open" if filename else "gateway.adapters.linux.os.fdopen"
            opener = real_open if filename else real_fdopen
            with self.subTest(filename=filename), patch("tempfile.tempdir", str(self.directory)), \
                    patch(target, side_effect=lambda *args, opener=opener, **kwargs: failed_write(*args, opener=opener, **kwargs)), \
                    self.assertRaisesRegex(OSError, "disk full"):
                self.adapter._write_temp_file(b"payload", suffix=".png", filename=filename)
            self.assertEqual(list(self.directory.iterdir()), [])


class LinuxConsoleFailureTest(unittest.TestCase):
    def test_unresolved_window_preserves_existing_cache(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            original = json.dumps({"version": 2, "windows": [{"name": "group", "conversation_id": "group@chatroom"}]})
            path.write_text(original)
            adapter = Mock()
            adapter.ui_operation_lock = threading.RLock()
            console = LinuxConsoleAdapter(adapter)
            console._cache_path = Mock(return_value=path)
            console._windows = Mock(return_value=[LinuxWeChatWindow("group", "0x1")])
            result = console.cache_standalone_windows()
            self.assertFalse(result["available"])
            self.assertIn("保留原缓存", result["message"])
            self.assertEqual(path.read_text(), original)

    def test_cached_windows_reject_missing_or_invalid_ids(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cache.json"
            console = LinuxConsoleAdapter(Mock())
            console._cache_path = Mock(return_value=path)
            for value in [None, "", " ", 123]:
                path.write_text(json.dumps({"version": 2, "windows": [{"name": "group", "conversation_id": value}]}))
                self.assertFalse(console.get_cached_standalone_windows()["available"])

    def test_tts_holds_console_lock_but_not_ui_lock(self):
        adapter = Mock()
        adapter.ui_operation_lock = threading.Lock()
        console = LinuxConsoleAdapter(adapter)
        console._send_message = Mock(return_value={"available": True})
        entered, finish = threading.Event(), threading.Event()
        result = []

        def synthesis(text):
            entered.set()
            if not finish.wait(3):
                raise TimeoutError("test did not release synthesis")
            return "audio", "tts.mp3"

        with patch("gateway.console.adapters.linux.synthesize_voice", side_effect=synthesis):
            worker = threading.Thread(target=lambda: result.append(console.send_message(
                message_type="voice", message="text", filename="", chat_name="group")))
            worker.start()
            try:
                self.assertTrue(entered.wait(1))
                rejected = console.open_chat(path="unused")
                self.assertFalse(rejected["available"])
                self.assertIn("正在执行其他操作", rejected["message"])
                self.assertTrue(adapter.ui_operation_lock.acquire(blocking=False))
                adapter.ui_operation_lock.release()
                adapter.window_nodes.assert_not_called()
            finally:
                finish.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertTrue(result[0]["available"])

    def test_tts_failure_releases_console_lock(self):
        console = LinuxConsoleAdapter(Mock())
        with patch("gateway.console.adapters.linux.synthesize_voice", side_effect=RuntimeError("tts unavailable")):
            self.assertFalse(console.send_message(message_type="voice", message="text", filename="", chat_name="group")["available"])
        self.assertTrue(console._console_lock.acquire(blocking=False))
        console._console_lock.release()

    def test_restore_reports_partial_and_total_failure(self):
        for partial in [False, True]:
            with self.subTest(partial=partial):
                adapter = Mock()
                adapter.ui_operation_lock = threading.RLock()
                adapter._talker_to_title = {"good": "group"}
                adapter._title_to_talker = {"group": "good"}
                adapter.window_nodes.return_value = [
                    {"role": "list", "name": "Chats", "path": "0", "depth": 0},
                    {"name": "group", "path": "0.0", "depth": 1},
                ]
                console = LinuxConsoleAdapter(adapter)
                cached = [{"name": "missing"}]
                if partial:
                    cached.append({"name": "group", "conversation_id": "good"})
                console.get_cached_standalone_windows = Mock(return_value={"available": True, "exists": True, "windows": cached})
                console._main = Mock()
                console._activate = Mock()
                console._scroll = Mock()
                console._open_standalone = Mock(return_value={"name": "group", "status": "already_open"})
                console.list_standalone_windows = Mock(return_value={"windows": []})
                result = console.open_cached_standalone_windows()
                self.assertFalse(result["available"])
                self.assertEqual(result["failed_count"], 1)
                self.assertEqual(result["already_open_count"], int(partial))
                self.assertEqual(result["cached_count"], 1 + int(partial))


class LinuxRecordingFailureTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.marker = Path(directory.name) / "pending.json"
        self.adapter = object.__new__(LinuxWeChatAdapter)
        self.adapter._recording_marker_path = Mock(return_value=self.marker)
        self.adapter._send_lock = threading.RLock()
        self.window = LinuxWeChatWindow("group", "0x1", "group@chatroom")

    def test_failed_cancel_persists_and_blocks_a_new_adapter(self):
        adapter = self.adapter
        adapter.SUPPORTS_VOICE = True
        adapter._activate_window = Mock(return_value=True)
        adapter._focus_input_area = Mock(return_value=True)
        adapter._send_context_matches = Mock(return_value=True)
        adapter._keys = Mock(return_value=True)
        adapter._recording_stopped = Mock(return_value=False)
        adapter._voice_button = Mock(side_effect=[True, False])
        retained = []

        @contextlib.contextmanager
        def microphone(*, retain_modules):
            try:
                yield "sink", "source_mic", "original_source"
            finally:
                retained.append(retain_modules())

        with patch.object(linux_voice, "decoded_voice", return_value=contextlib.nullcontext((Path("voice.wav"), 1))), \
                patch.object(linux_voice, "microphone", microphone), \
                patch.object(linux_voice, "connect_wechat_microphone", return_value=True), \
                patch.object(linux_voice, "play_voice", side_effect=RuntimeError("focus lost")), \
                patch("gateway.adapters.linux.time.sleep"), \
                patch("gateway.adapters.linux.time.monotonic", side_effect=[0, 0, 0, 0, 0, 0, 3]):
            self.assertFalse(adapter.send_voice_to_window(self.window, "audio", "voice.mp3"))
        self.assertEqual(retained, [True])
        self.assertTrue(self.marker.exists())
        self.assertEqual(json.loads(self.marker.read_text())["audio_source"], "source_mic")
        restarted = object.__new__(LinuxWeChatAdapter)
        restarted._recording_marker_path = Mock(return_value=self.marker)
        restarted._send_lock = threading.RLock()
        restarted._finish_recording = Mock(return_value=False)
        with self.assertRaisesRegex(RuntimeError, "暂停 UI 操作"), restarted.ui_operation_lock:
            self.fail("an unresolved recording allowed UI access")

    def test_confirmed_stop_cleans_retained_devices_and_marker(self):
        self.adapter._mark_recording(self.window, "source_mic")
        self.adapter._recording_stopped = Mock(return_value=True)
        self.adapter._voice_button = Mock()
        with patch.object(linux_voice, "cleanup_microphone") as cleanup, self.adapter.ui_operation_lock:
            self.assertFalse(self.marker.exists())
            cleanup.assert_called_once_with("source_mic", None)
        self.adapter._voice_button.assert_not_called()

    def test_send_cleanup_failure_keeps_recovery_marker(self):
        adapter = self.adapter
        adapter.SUPPORTS_VOICE = True
        for method in ["_activate_window", "_focus_input_area", "_send_context_matches", "_keys", "_voice_button", "_recording_stopped"]:
            setattr(adapter, method, Mock(return_value=True))
        with patch.object(linux_voice, "_pactl", return_value="original"), \
                patch.object(linux_voice, "decoded_voice", return_value=contextlib.nullcontext((Path("voice.wav"), 1))), \
                patch.object(linux_voice, "connect_wechat_microphone", return_value=True), \
                patch.object(linux_voice, "play_voice"), patch("gateway.adapters.linux.time.sleep"), \
                patch.object(linux_voice, "cleanup_microphone", side_effect=RuntimeError("restore failed")) as cleanup:
            self.assertFalse(adapter.send_voice_to_window(self.window, "audio", "voice.mp3"))
            cleanup.assert_called_once()
        self.assertTrue(self.marker.exists())
        with patch.object(linux_voice, "cleanup_microphone") as cleanup, adapter.ui_operation_lock:
            self.assertFalse(self.marker.exists())
            cleanup.assert_called_once()

    def test_microphone_cleanup_error_is_propagated(self):
        with patch.object(linux_voice, "_pactl", return_value="original"), \
                patch.object(linux_voice, "cleanup_microphone", side_effect=RuntimeError("restore failed")), \
                self.assertRaisesRegex(RuntimeError, "restore failed"), linux_voice.microphone():
            pass

    def test_successful_send_cleans_microphone_once_before_removing_marker(self):
        adapter = self.adapter
        adapter.SUPPORTS_VOICE = True
        for method in ["_activate_window", "_focus_input_area", "_send_context_matches", "_keys", "_voice_button", "_recording_stopped"]:
            setattr(adapter, method, Mock(return_value=True))
        with patch.object(linux_voice, "_pactl", return_value="original"), \
                patch.object(linux_voice, "decoded_voice", return_value=contextlib.nullcontext((Path("voice.wav"), 1))), \
                patch.object(linux_voice, "connect_wechat_microphone", return_value=True), \
                patch.object(linux_voice, "play_voice"), patch("gateway.adapters.linux.time.sleep"), \
                patch.object(linux_voice, "cleanup_microphone", side_effect=lambda *args: self.assertTrue(self.marker.exists())) as cleanup:
            self.assertTrue(adapter.send_voice_to_window(self.window, "audio", "voice.mp3"))
            cleanup.assert_called_once()
        self.assertFalse(self.marker.exists())

    def test_retained_microphone_keeps_silent_default_until_cleanup(self):
        calls = []
        default = ["original"]

        def pactl(*args):
            calls.append(args)
            if args[0] == "get-default-source":
                return default[0]
            if args[0] == "set-default-source":
                default[0] = args[1]
            if args[0] == "load-module":
                return "10" if args[1] == "module-null-sink" else "11"
            if args == ("list", "short", "modules"):
                return (f"10\tmodule-null-sink\tsink_name={sink}\n"
                        f"11\tmodule-remap-source\tsource_name={source}\n12\tunrelated\tsource_name=other")
            return ""

        with patch.object(linux_voice, "_pactl", side_effect=pactl):
            with linux_voice.microphone(retain_modules=lambda: True) as (sink, source, previous):
                self.assertEqual(previous, "original")
            self.assertEqual(default[0], source)
            self.assertFalse(any(call[0] == "unload-module" for call in calls))
            linux_voice.cleanup_microphone(source, previous)
        self.assertEqual(default[0], "original")
        self.assertEqual([call[1] for call in calls if call[0] == "unload-module"], ["11", "10"])

    def test_live_stream_or_recording_ui_prevents_confirmation(self):
        self.window.Exists = Mock(return_value=True)
        self.adapter.window_nodes = Mock(return_value=[
            {"role": "push button", "name": "Send Voice"}, {"role": "push button", "name": "Cancel"},
        ])
        with patch.object(linux_voice, "wechat_microphone_streams", return_value=[]):
            self.assertFalse(self.adapter._recording_stopped(self.window))
        self.adapter.window_nodes.return_value = [{"role": "push button", "name": "Send Voice"}]
        with patch.object(linux_voice, "wechat_microphone_streams", return_value=[{"index": 1}]):
            self.assertFalse(self.adapter._recording_stopped(self.window))
        with patch.object(linux_voice, "wechat_microphone_streams", return_value=[]):
            self.assertTrue(self.adapter._recording_stopped(self.window))

    def test_stop_can_be_confirmed_after_user_changes_chat(self):
        self.window.title = "WeChat"
        self.window.expected_chat = "original"
        self.window.Exists = Mock(return_value=True)
        self.adapter.window_nodes = Mock(return_value=[
            {"role": "text", "name": "other chat", "rect": {"top": 1, "width": 10, "height": 10}},
            {"role": "push button", "name": "Send Voice"},
        ])
        with patch.object(linux_voice, "wechat_microphone_streams", return_value=[]):
            self.assertTrue(self.adapter._recording_stopped(self.window))


if __name__ == "__main__":
    unittest.main()
