import contextlib
import ctypes
import os
from pathlib import Path

from gateway.startup.adapters.base import MAIN_TITLES, BaseLoginAdapter, LoginSnapshot, classify_login_nodes


class WindowsLoginAdapter(BaseLoginAdapter):
    PROCESS_NAMES = frozenset({"wechat.exe", "weixin.exe"})

    @staticmethod
    def _session_id(pid):
        value = ctypes.c_ulong()
        if not ctypes.windll.kernel32.ProcessIdToSessionId(pid, ctypes.byref(value)):
            raise OSError("无法查询微信桌面会话")
        return value.value

    def _same_session(self, process):
        return self._session_id(process.pid) == self._session_id(os.getpid())

    def session_ready(self):
        return self._session_id(os.getpid()) != 0

    def _find_executable(self):
        import winreg

        for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
            for app in ("Weixin", "WeChat"):
                for view in (winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY):
                    with contextlib.suppress(OSError):
                        with winreg.OpenKey(root, rf"Software\Tencent\{app}", 0, winreg.KEY_READ | view) as key:
                            directory, _ = winreg.QueryValueEx(key, "InstallPath")
                        for name in ("Weixin.exe", "WeChat.exe"):
                            path = Path(directory) / name
                            if path.is_file():
                                return path
        for env in ("ProgramFiles", "ProgramFiles(x86)", "LOCALAPPDATA"):
            if not os.environ.get(env):
                continue
            for app in ("Weixin", "WeChat"):
                path = Path(os.environ[env]) / "Tencent" / app / (app + ".exe")
                if path.is_file():
                    return path
        return None

    def _launch(self, executable):
        import pythoncom
        from win32com.client import GetObject

        pythoncom.CoInitializeEx(pythoncom.COINIT_MULTITHREADED)
        try:
            service = GetObject(r"winmgmts:{impersonationLevel=impersonate}!\\.\root\cimv2")
            startup = service.Get("Win32_ProcessStartup").SpawnInstance_()
            startup.ShowWindow = 1
            startup.WinstationDesktop = r"winsta0\default"
            startup.CreateFlags = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB: 脱离 WMI 提供程序的 Job。
            process = service.Get("Win32_Process")
            params = process.Methods_("Create").InParameters.SpawnInstance_()
            params.CommandLine = f'"{executable}"'
            params.CurrentDirectory = str(Path(executable).parent)
            params.ProcessStartupInformation = startup
            result = process.ExecMethod_("Create", params)
            if result.ReturnValue != 0:
                raise RuntimeError(f"WMI 启动微信失败,错误码 {result.ReturnValue}")
        finally:
            result = params = process = startup = service = None
            pythoncom.CoUninitialize()

    def _probe_locked(self):
        auto = self.console._ensure_uia()
        pids = {p.pid for p in self.processes()}
        windows = [w for w in self.console._iter_desktop_windows(auto) if w.ProcessId in pids and w.Name in MAIN_TITLES]
        if len(windows) != 1:
            return LoginSnapshot("transition")
        window = windows[0]
        nodes = self.console._collect_nodes(window, max_depth=18, max_nodes=2000)
        nodes = [{**n, "role": n["control_type"]} for n in nodes]
        visible = [n for n in nodes if not n.get("offscreen") and n.get("rect")
                   and n["rect"]["width"] > 0 and n["rect"]["height"] > 0]
        chat_list = self.console._chat_list_node(visible) is not None
        state, button = classify_login_nodes(nodes, chat_list=chat_list)
        return LoginSnapshot(state, window, button)

    def _activate(self, window):
        if not self.console._activate_window_locked(window):
            raise RuntimeError("激活微信登录窗口失败")

    def _click(self, snapshot):
        import win32gui

        if win32gui.GetForegroundWindow() != snapshot.window.NativeWindowHandle:
            raise RuntimeError("点击登录前微信失去焦点")
        snapshot.button["_control"].Click(waitTime=0)

    def _standalone_windows(self):
        return self.console._standalone_windows(self.console._ensure_uia())

    def _maximize(self, window):
        import win32con
        import win32gui

        win32gui.ShowWindow(window.NativeWindowHandle, win32con.SW_MAXIMIZE)
        if win32gui.GetWindowPlacement(window.NativeWindowHandle)[1] != win32con.SW_MAXIMIZE:
            raise RuntimeError("微信窗口未最大化")
