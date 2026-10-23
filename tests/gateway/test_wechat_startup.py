import sys
import threading
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from common.config import Config, config
from gateway.startup import WeChatStartup
from gateway.startup.adapters.base import LoginSnapshot, classify_login_nodes
from gateway.startup.adapters.linux import LinuxLoginAdapter, linux_launch_env
from gateway.startup.adapters.windows import WindowsLoginAdapter


class StartupTests(unittest.TestCase):
    def setUp(self):
        self.enabled = patch.object(config, "WECHAT_AUTO_LOGIN_ENABLED", True)
        self.enabled.start()
        self.addCleanup(self.enabled.stop)
        self.console = MagicMock()
        self.console.get_cached_standalone_windows.return_value = {"available": True, "exists": True, "windows": [{}]}
        self.console.open_cached_standalone_windows.return_value = {"available": True}
        self.worker = WeChatStartup(MagicMock(), self.console)
        self.worker._stop.wait = MagicMock(return_value=False)
        self.factory = patch("gateway.startup.get_login_adapter")
        self.driver_class = self.factory.start()
        self.driver = self.driver_class.return_value
        self.driver.processes.return_value = [object()]
        self.driver.session_ready.return_value = True
        self.addCleanup(self.factory.stop)

    def run_states(self, *states):
        self.driver.probe.side_effect = [LoginSnapshot(s) for s in states]
        with patch.object(self.worker, "_watch_loop", return_value=False):
            self.worker._run()

    def test_default_disabled_no_driver_or_thread(self):
        self.assertFalse(Config(_env_file=None).WECHAT_AUTO_LOGIN_ENABLED)
        with patch.object(config, "WECHAT_AUTO_LOGIN_ENABLED", False):
            self.worker.start()
        self.driver_class.assert_not_called()
        self.assertIsNone(self.worker._thread)

    def test_click_once_then_restore_after_two_ready_samples(self):
        self.driver.click_login.return_value = "clicked"
        self.run_states("login_button", "login_button", "logged_in", "logged_in")
        self.driver.launch.assert_called_once()
        self.driver.click_login.assert_called_once()
        self.console.open_cached_standalone_windows.assert_called_once()
        self.driver.maximize_windows.assert_called_once()
        self.assertEqual(self.worker.status["state"], "ready")

    def test_qr_before_or_after_click_stops_without_restore_or_cache(self):
        for states in (("qr_required",), ("login_button", "qr_required")):
            with self.subTest(states=states):
                self.console.reset_mock()
                self.driver.click_login.return_value = "clicked"
                self.run_states(*states)
                self.assertEqual(self.worker.status["state"], "qr_required")
                self.console.open_cached_standalone_windows.assert_not_called()
                self.console.cache_standalone_windows.assert_not_called()

    def test_qr_appears_during_click_recheck(self):
        self.driver.click_login.return_value = "qr_required"
        self.run_states("login_button")
        self.assertEqual(self.worker.status["state"], "qr_required")
        self.console.open_cached_standalone_windows.assert_not_called()

    def test_already_logged_in_no_login_click(self):
        self.run_states("logged_in", "logged_in")
        self.driver.click_login.assert_not_called()
        self.console.open_cached_standalone_windows.assert_called_once()

    def test_no_cache_skips_restore_without_saving(self):
        self.console.get_cached_standalone_windows.return_value = {"available": True, "exists": False}
        self.run_states("logged_in", "logged_in")
        self.console.open_cached_standalone_windows.assert_not_called()
        self.assertEqual(self.worker.status["state"], "ready")

    def test_locked_preserves_cache(self):
        self.run_states("locked")
        self.console.cache_standalone_windows.assert_not_called()
        self.assertEqual(self.worker.status["state"], "locked")

    def test_temporary_accessibility_failure_is_retried(self):
        self.driver.probe.side_effect = [RuntimeError("not registered"), LoginSnapshot("logged_in"), LoginSnapshot("logged_in")]
        with patch.object(self.worker, "_watch_loop", return_value=False):
            self.worker._run()
        self.assertEqual(self.worker.status["state"], "ready")

    def test_failed_restore_preserves_cache(self):
        self.console.open_cached_standalone_windows.return_value = {"available": False, "message": "missing chat"}
        self.driver.probe.return_value = LoginSnapshot("logged_in")
        with patch("gateway.startup.time.monotonic", side_effect=[0, 1, 121]):
            self.worker._run()
        self.assertEqual(self.worker.status["state"], "failed")
        self.console.cache_standalone_windows.assert_not_called()

    def test_timeout_does_not_repeat_login_click(self):
        self.driver.click_login.return_value = "clicked"
        self.driver.probe.return_value = LoginSnapshot("login_button")
        with patch("gateway.startup.time.monotonic", side_effect=[0, 1, 121]):
            self.worker._run()
        self.driver.click_login.assert_called_once()
        self.assertEqual(self.worker.status["state"], "timeout")

    def test_stop_before_start_no_launch(self):
        self.worker._stop.set()
        self.worker._run()
        self.driver.launch.assert_not_called()

    def test_watch_never_saves_after_logout(self):
        self.driver.probe.return_value = LoginSnapshot("qr_required")
        self.worker._watch_loop(self.driver)
        self.console.cache_standalone_windows.assert_not_called()

    def test_watch_never_saves_and_stop_interrupts_wait(self):
        self.driver.probe.return_value = LoginSnapshot("logged_in")
        self.worker._stop.wait.return_value = True
        self.worker._watch_loop(self.driver)
        self.console.cache_standalone_windows.assert_not_called()
        self.worker._stop.wait.assert_called_once_with(5)


class LoginDriverTests(unittest.TestCase):
    @staticmethod
    def node(name, role="push button", **extra):
        return {"name": name, "role": role, "rect": {"left": 0, "top": 0, "right": 100, "bottom": 40,
                                                      "width": 100, "height": 40}, "enabled": True, **extra}

    def test_qr_has_priority_over_login_button(self):
        nodes = [self.node("Open WeChat"), self.node("Scan QR code", "label")]
        self.assertEqual(classify_login_nodes(nodes, chat_list=False)[0], "qr_required")

    def test_chat_message_mentioning_qr_does_not_log_out(self):
        self.assertEqual(classify_login_nodes([self.node("扫码登录", "list item")], chat_list=True)[0], "logged_in")

    def test_disabled_offscreen_duplicate_buttons_not_clicked(self):
        for nodes in ([self.node("登录", enabled=False)], [self.node("Login", offscreen=True)],
                      [self.node("登录"), self.node("登录")]):
            self.assertEqual(classify_login_nodes(nodes, chat_list=False)[0], "transition")
        self.assertEqual(classify_login_nodes([self.node("Open WeChat")], chat_list=False)[0], "login_button")

    def test_login_labels_support_chinese_and_english_brand_names(self):
        for name in ('进入微信', '进入WeChat', '进入 Weixin', '登录WeChat', 'Open WeChat', 'Open Weixin', 'Log In'):
            with self.subTest(name=name):
                self.assertEqual(classify_login_nodes([self.node(name)], chat_list=False)[0], 'login_button')

    def test_click_rechecks_state_under_both_locks(self):
        adapter = MagicMock()
        adapter.ui_operation_lock = threading.RLock()
        console = MagicMock()
        console._console_lock = threading.Lock()
        driver = LinuxLoginAdapter(adapter, console)
        with patch.object(driver, "_probe_locked", side_effect=[LoginSnapshot("login_button"), LoginSnapshot("qr_required")]), \
             patch.object(driver, "_activate"):
            self.assertEqual(driver.click_login(), "qr_required")
        adapter._run_x11.assert_not_called()
        self.assertTrue(console._console_lock.acquire(blocking=False))

    def test_existing_process_not_launched_twice(self):
        adapter = MagicMock()
        adapter.ui_operation_lock = nullcontext()
        driver = LinuxLoginAdapter(adapter, MagicMock())
        with patch.object(driver, "processes", return_value=[object()]), patch.object(driver, "executable") as executable:
            driver.launch()
        executable.assert_not_called()

    def test_invalid_explicit_executable_fails_without_fallback(self):
        driver = LinuxLoginAdapter(MagicMock(), MagicMock())
        with patch.object(config, "WECHAT_EXECUTABLE_PATH", "/missing/wechat"), self.assertRaises(FileNotFoundError):
            driver.executable()


class StartupPlatformTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows UIA adapter")
    def test_windows_activation_restores_only_minimized_windows(self):
        from gateway.console.adapters.windows.adapter import WindowsConsoleAdapter

        console = WindowsConsoleAdapter(MagicMock())
        for minimized in (False, True):
            gui = MagicMock()
            gui.IsIconic.return_value = minimized
            gui.GetForegroundWindow.return_value = 123
            with patch.dict("sys.modules", {"win32gui": gui}), patch("time.sleep"):
                self.assertTrue(console._activate_window_by_hwnd_locked(SimpleNamespace(NativeWindowHandle=123)))
            self.assertEqual(gui.ShowWindow.call_count, int(minimized))

    def test_linux_maximizes_standalone_and_main_last(self):
        adapter = MagicMock()
        adapter.ui_operation_lock = threading.Lock()
        adapter.list_windows.return_value = ["single-a", "single-b"]
        driver = LinuxLoginAdapter(adapter, MagicMock())
        with patch.object(driver, "_probe_locked", return_value=LoginSnapshot("logged_in", "main")), \
             patch.object(driver, "_activate"), patch.object(driver, "_maximize") as maximize:
            driver.maximize_windows()
        self.assertEqual(maximize.call_args_list, [unittest.mock.call("single-a"),
                                                  unittest.mock.call("single-b"), unittest.mock.call("main")])

    def test_windows_launch_is_delegated_to_local_wmi(self):
        driver = WindowsLoginAdapter(MagicMock(), MagicMock())
        com, client, service, startup, process = [MagicMock() for _ in range(5)]
        client.GetObject.return_value = service
        service.Get.side_effect = [MagicMock(SpawnInstance_=MagicMock(return_value=startup)), process]
        process.ExecMethod_.return_value.ReturnValue = 0
        with patch.dict('sys.modules', {'pythoncom': com, 'win32com.client': client}), \
             patch('subprocess.Popen') as direct_launch:
            driver._launch('C:/Program Files/微信/Weixin.exe')
        client.GetObject.assert_called_once_with(r'winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2')
        params = process.ExecMethod_.call_args.args[1]
        self.assertEqual(params.CommandLine, '"C:/Program Files/微信/Weixin.exe"')
        self.assertEqual(params.CurrentDirectory, str(Path('C:/Program Files/微信')))
        self.assertIs(params.ProcessStartupInformation, startup)
        self.assertEqual(startup.WinstationDesktop, r'winsta0\default')
        self.assertEqual(startup.CreateFlags, 0x01000000)
        self.assertEqual(startup.ShowWindow, 1)
        com.CoInitializeEx.assert_called_once_with(com.COINIT_MULTITHREADED)
        com.CoUninitialize.assert_called_once()
        direct_launch.assert_not_called()

    def test_windows_wmi_failure_releases_com_without_subprocess_fallback(self):
        for failure in ('connection', 'creation'):
            with self.subTest(failure=failure):
                driver = WindowsLoginAdapter(MagicMock(), MagicMock())
                com, client, service = [MagicMock() for _ in range(3)]
                client.GetObject.return_value = service
                if failure == 'connection':
                    client.GetObject.side_effect = RuntimeError('WMI unavailable')
                else:
                    service.Get.return_value.ExecMethod_.return_value.ReturnValue = 2
                with patch.dict('sys.modules', {'pythoncom': com, 'win32com.client': client}), \
                     patch('subprocess.Popen') as direct_launch, self.assertRaises(RuntimeError):
                    driver._launch('C:/Weixin/Weixin.exe')
                com.CoInitializeEx.assert_called_once_with(com.COINIT_MULTITHREADED)
                com.CoUninitialize.assert_called_once()
                direct_launch.assert_not_called()

    def test_linux_launch_always_sets_accessibility_and_desktop_bus(self):
        desktop = MagicMock()
        desktop.info = {"name": "xfce4-session"}
        desktop.username.return_value = "awsl"
        desktop.environ.return_value = {"DISPLAY": ":1", "DBUS_SESSION_BUS_ADDRESS": "desktop-bus"}
        for previous in (None, "0"):
            env = {"DISPLAY": ":1.0", "DBUS_SESSION_BUS_ADDRESS": "service-bus"}
            if previous is not None:
                env["QT_LINUX_ACCESSIBILITY_ALWAYS_ON"] = previous
            with patch("gateway.startup.adapters.linux._x11_env", return_value=env), \
                 patch("gateway.startup.adapters.linux.psutil.process_iter", return_value=[desktop]), \
                 patch("gateway.startup.adapters.linux.psutil.Process") as current:
                current.return_value.username.return_value = "awsl"
                actual = linux_launch_env()
            self.assertEqual(actual["QT_LINUX_ACCESSIBILITY_ALWAYS_ON"], "1")
            self.assertEqual(actual["DBUS_SESSION_BUS_ADDRESS"], "desktop-bus")

    def test_windows_click_uses_non_reentrant_send_lock_without_deadlock(self):
        adapter = MagicMock()
        adapter.ui_operation_lock = threading.Lock()
        console = MagicMock()
        console._console_lock = threading.Lock()
        driver = WindowsLoginAdapter(adapter, console)
        control = MagicMock()
        snapshot = LoginSnapshot("login_button", SimpleNamespace(NativeWindowHandle=123), {"_control": control})
        gui = MagicMock()
        gui.GetForegroundWindow.return_value = 123
        result = []
        with patch.object(driver, "_probe_locked", return_value=snapshot), patch.object(driver, "_activate"), \
             patch.dict("sys.modules", {"win32gui": gui}):
            thread = threading.Thread(target=lambda: result.append(driver.click_login()), daemon=True)
            thread.start()
            thread.join(timeout=1)
        self.assertFalse(thread.is_alive())
        self.assertEqual(result, ["clicked"])
        control.Click.assert_called_once()

    def test_login_operations_wait_for_console_on_both_platforms(self):
        for platform in (LinuxLoginAdapter, WindowsLoginAdapter):
            for operation in ('launch', 'probe', 'click_login', 'maximize_windows'):
                with self.subTest(platform=platform.__name__, operation=operation):
                    console = MagicMock()
                    console._console_lock = threading.Lock()
                    console._console_lock.acquire()
                    adapter = MagicMock()
                    driver = platform(adapter, console)
                    errors = []

                    def run(driver=driver, operation=operation, errors=errors):
                        try:
                            getattr(driver, operation)()
                        except Exception as exc:
                            errors.append(exc)

                    state = 'login_button' if operation == 'click_login' else 'logged_in'
                    with patch.object(driver, 'processes', return_value=[object()]), \
                         patch.object(driver, '_probe_locked', return_value=LoginSnapshot(state)) as probe, \
                         patch.object(driver, '_activate'), patch.object(driver, '_maximize'), patch.object(driver, '_click'):
                        thread = threading.Thread(target=run, daemon=True)
                        thread.start()
                        try:
                            thread.join(timeout=.05)
                            self.assertTrue(thread.is_alive())
                            adapter.ui_operation_lock.__enter__.assert_not_called()
                            probe.assert_not_called()
                        finally:
                            console._console_lock.release()
                            thread.join(timeout=1)
                        self.assertFalse(thread.is_alive())
                        self.assertEqual(errors, [])
                        self.assertTrue(console._console_lock.acquire(blocking=False))
                        console._console_lock.release()

    def test_stop_cancels_console_wait_without_failing_startup(self):
        console = MagicMock()
        console._console_lock = threading.Lock()
        console._console_lock.acquire()
        adapter = MagicMock()
        worker = WeChatStartup(adapter, console)
        driver = LinuxLoginAdapter(adapter, console, cancelled=worker._stop.is_set)
        with patch('gateway.startup.get_login_adapter', return_value=driver), \
             patch.object(driver, 'session_ready', return_value=True):
            thread = threading.Thread(target=worker._run, daemon=True)
            thread.start()
            try:
                thread.join(timeout=.05)
                self.assertTrue(thread.is_alive())
                worker._stop.set()
                thread.join(timeout=1)
                self.assertFalse(thread.is_alive())
                self.assertNotEqual(worker.status['state'], 'failed')
                adapter.ui_operation_lock.__enter__.assert_not_called()
            finally:
                worker._stop.set()
                console._console_lock.release()
                thread.join(timeout=1)

    def test_maximize_refuses_login_or_qr_window(self):
        adapter = MagicMock()
        adapter.ui_operation_lock = threading.Lock()
        driver = LinuxLoginAdapter(adapter, MagicMock())
        for state in ("login_button", "qr_required", "transition"):
            with patch.object(driver, "_probe_locked", return_value=LoginSnapshot(state)), self.assertRaises(RuntimeError):
                driver.maximize_windows()
        adapter._run_x11.assert_not_called()

    def test_windows_maximize_includes_standalone_and_main(self):
        adapter = MagicMock()
        adapter.ui_operation_lock = threading.Lock()
        driver = WindowsLoginAdapter(adapter, MagicMock())
        snapshot = LoginSnapshot("logged_in", SimpleNamespace(NativeWindowHandle=123))
        driver.console._standalone_windows.return_value = [SimpleNamespace(NativeWindowHandle=456)]
        gui = MagicMock(spec=['ShowWindow', 'GetWindowPlacement'])
        gui.GetWindowPlacement.return_value = (0, 3, (0, 0), (0, 0), (0, 0, 1920, 1040))
        with patch.object(driver, "_probe_locked", return_value=snapshot), patch.object(driver, "_activate"), \
             patch.dict("sys.modules", {"win32gui": gui, "win32con": SimpleNamespace(SW_MAXIMIZE=3)}):
            driver.maximize_windows()
        self.assertEqual(gui.ShowWindow.call_args_list, [unittest.mock.call(456, 3), unittest.mock.call(123, 3)])
        self.assertEqual(gui.GetWindowPlacement.call_args_list, [unittest.mock.call(456), unittest.mock.call(123)])

    def test_manual_start_allowed_when_auto_disabled_and_deduplicated(self):
        with patch.object(config, "WECHAT_AUTO_LOGIN_ENABLED", False), patch("gateway.startup.threading.Thread") as thread:
            worker = WeChatStartup(MagicMock(), MagicMock())
            worker.supported = True
            self.assertTrue(worker.start(manual=True))
            thread.return_value.is_alive.return_value = True
            self.assertFalse(worker.start(manual=True))
            thread.return_value.start.assert_called_once()

    def test_startup_api_authentication_and_manual_request(self):
        from gateway.routes import console

        startup = MagicMock()
        startup.start.return_value = True
        startup.supported = True
        startup.status = {"enabled": False, "state": "starting", "supported": True}
        with patch.object(console, "router", console.APIRouter()), patch.object(config, "HTTP_API_TOKEN", "startup-test-token"):
            app = FastAPI()
            app.include_router(console.create_routes(SimpleNamespace(wechat_startup=startup)))
            with TestClient(app) as client:
                self.assertEqual(client.post("/api/console/wechat-startup").status_code, 401)
                headers = {"Authorization": "Bearer startup-test-token"}
                self.assertEqual(client.post("/api/console/wechat-startup", headers=headers).status_code, 200)
                startup.start.assert_called_once_with(manual=True)
                startup.supported = False
                self.assertEqual(client.post("/api/console/wechat-startup", headers=headers).status_code, 501)


if __name__ == "__main__":
    unittest.main()
