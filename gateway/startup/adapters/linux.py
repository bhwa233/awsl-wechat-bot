import contextlib
import shutil
import subprocess
import time
from pathlib import Path

import psutil

from gateway.adapters.linux import _x11_env
from gateway.startup.adapters.base import MAIN_TITLES, BaseLoginAdapter, LoginSnapshot, classify_login_nodes


def linux_launch_env():
    env = _x11_env()
    sessions = set()
    desktop_names = {"xfce4-session", "gnome-shell", "gnome-session-binary", "plasmashell", "lxqt-session",
                     "mate-session", "cinnamon-session"}
    for process in psutil.process_iter(["name", "username"]):
        if process.info["name"] not in desktop_names:
            continue
        with contextlib.suppress(psutil.Error):
            if process.username() != psutil.Process().username():
                continue
            desktop = process.environ()
            if desktop.get("DISPLAY", "").removesuffix(".0") != env["DISPLAY"].removesuffix(".0"):
                continue
            if desktop.get("DBUS_SESSION_BUS_ADDRESS"):
                sessions.add(desktop["DBUS_SESSION_BUS_ADDRESS"])
    if len(sessions) > 1:
        raise RuntimeError("同一显示器存在多个桌面 D-Bus 会话,停止启动微信")
    if not sessions:
        raise RuntimeError("等待匹配的 X11 桌面 D-Bus 会话")
    env["DBUS_SESSION_BUS_ADDRESS"] = sessions.pop()
    env["QT_LINUX_ACCESSIBILITY_ALWAYS_ON"] = "1"
    return env

class LinuxLoginAdapter(BaseLoginAdapter):
    PROCESS_NAMES = frozenset({"wechat", "weixin"})

    def session_ready(self):
        try:
            if self.adapter._run_x11(["xdotool", "getdisplaygeometry"], timeout=3).returncode != 0:
                return False
            linux_launch_env()
        except (OSError, subprocess.SubprocessError, RuntimeError):
            return False
        return True

    def _find_executable(self):
        for name in ("wechat", "weixin"):
            executable = shutil.which(name)
            if executable:
                return Path(executable)
        return None

    def _launch(self, executable):
        env = linux_launch_env()
        args = ["systemd-run", "--user", "--collect", "--quiet", f"--unit=awsl-wechat-autostart-{time.time_ns()}"]
        for name in ("DISPLAY", "XAUTHORITY", "DBUS_SESSION_BUS_ADDRESS", "QT_LINUX_ACCESSIBILITY_ALWAYS_ON"):
            if env.get(name):
                args.append(f"--setenv={name}={env[name]}")
        subprocess.run([*args, "--", executable], env=env, capture_output=True, check=True, timeout=10)

    def _probe_locked(self):
        windows = [w for w in self.adapter.list_windows(include_main=True) if w.title in MAIN_TITLES]
        if len(windows) != 1:
            return LoginSnapshot("transition")
        window = windows[0]
        nodes = self.adapter.window_nodes(window, max_nodes=2000)
        chat_list = any(n["role"] == "list" and n["name"] in {"Chats", "聊天", "会话"}
                        and n["rect"] and n["rect"]["width"] > 0 and n["rect"]["height"] > 0 for n in nodes)
        state, button = classify_login_nodes(nodes, chat_list=chat_list)
        return LoginSnapshot(state, window, button)

    def _activate(self, window):
        self.console._activate(window)

    def _click(self, snapshot):
        if not self.adapter._active_window_matches(snapshot.window):
            raise RuntimeError("点击登录前微信失去焦点")
        rect = snapshot.button["rect"]
        x, y = (rect["left"] + rect["right"]) // 2, (rect["top"] + rect["bottom"]) // 2
        self.adapter._run_x11(["xdotool", "mousemove", "--sync", str(x), str(y), "mousedown", "1",
                               "sleep", "0.15", "mouseup", "1"], timeout=3).check_returncode()

    def _standalone_windows(self):
        return self.adapter.list_windows()

    def _maximize(self, window):
        self.adapter._run_x11(["wmctrl", "-i", "-r", window.window_id, "-b",
                               "add,maximized_vert,maximized_horz"]).check_returncode()
        for _ in range(10):
            state = self.adapter._run_x11(["xprop", "-id", window.window_id, "_NET_WM_STATE"])
            if (state.returncode == 0 and "_NET_WM_STATE_MAXIMIZED_VERT" in state.stdout
                    and "_NET_WM_STATE_MAXIMIZED_HORZ" in state.stdout):
                return
            time.sleep(.1)
        raise RuntimeError("微信窗口未最大化")
