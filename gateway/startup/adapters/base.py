from __future__ import annotations

import contextlib
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

import psutil

from common.config import config

LOGIN_NAMES = {"登录", "进入微信", "登录微信", "进入wechat", "进入weixin", "登录wechat", "登录weixin",
               "login", "enterwechat", "openwechat", "enterweixin", "openweixin"}
MAIN_TITLES = {"微信", "WeChat", "Weixin"}


@dataclass
class LoginSnapshot:
    state: str
    window: object = None
    button: object = None

def classify_login_nodes(nodes: list[dict], *, chat_list: bool) -> tuple[str, dict | None]:
    visible = [n for n in nodes if n.get("rect") and n["rect"]["width"] > 0 and n["rect"]["height"] > 0
               and not n.get("offscreen")]
    if any(re.fullmatch(r"(?:Linux )?(?:微信|WeChat|Weixin)(?:已被锁定| is locked)", n["name"], re.I) for n in visible):
        return "locked", None
    if chat_list:
        return "logged_in", None
    if any(re.search(r"二维码|扫码|QR\s*code|scan.*(?:code|log.?in)", n["name"], re.I) for n in visible):
        return "qr_required", None
    buttons = [n for n in visible if n.get("role") in {"push button", "ButtonControl"}
               and "".join(n["name"].split()).casefold() in LOGIN_NAMES and n.get("enabled") is True]
    if len(buttons) == 1:
        return "login_button", buttons[0]
    return "transition", None


class BaseLoginAdapter(ABC):
    PROCESS_NAMES = frozenset()

    def __init__(self, adapter, console, *, cancelled=lambda: False):
        self.adapter = adapter
        self.console = console
        self.cancelled = cancelled

    @contextlib.contextmanager
    def _operation(self):
        while not self.console._console_lock.acquire(timeout=.2):
            if self.cancelled():
                raise InterruptedError("微信启动已停止")
        try:
            if self.cancelled():
                raise InterruptedError("微信启动已停止")
            with self.adapter.ui_operation_lock:
                yield
        finally:
            self.console._console_lock.release()

    def processes(self):
        current = psutil.Process().username().casefold()
        found = []
        for process in psutil.process_iter(["name", "username"]):
            with contextlib.suppress(psutil.Error):
                if (process.info["name"] or "").casefold() not in self.PROCESS_NAMES:
                    continue
                if (process.info["username"] or "").casefold() != current or not self._same_session(process):
                    continue
                found.append(process)
        return found

    def _same_session(self, process):
        return True

    def session_ready(self):
        return True

    def executable(self) -> Path:
        if config.WECHAT_EXECUTABLE_PATH:
            path = Path(config.WECHAT_EXECUTABLE_PATH).expanduser()
            if not path.is_file():
                raise FileNotFoundError("WECHAT_EXECUTABLE_PATH 不是可用的微信程序路径")
            return path.resolve()
        path = self._find_executable()
        if path is None:
            raise FileNotFoundError("未找到微信程序,请配置 WECHAT_EXECUTABLE_PATH")
        return path

    def launch(self):
        with self._operation():
            if not self.cancelled() and not self.processes():
                self._launch(str(self.executable()))

    def probe(self) -> LoginSnapshot:
        with self._operation():
            if self.cancelled():
                return LoginSnapshot("stopped")
            return self._probe_locked()

    def click_login(self):
        with self._operation():
            snapshot = self._probe_locked()
            if snapshot.state != "login_button":
                return snapshot.state
            self._activate(snapshot.window)
            snapshot = self._probe_locked()
            if snapshot.state != "login_button":
                return snapshot.state
            if self.cancelled():
                return "stopped"
            self._click(snapshot)
            return "clicked"

    def maximize_windows(self):
        with self._operation():
            snapshot = self._probe_locked()
            if snapshot.state != "logged_in" or self.cancelled():
                raise RuntimeError("尚未确认登录,不最大化窗口")
            for window in self._standalone_windows():
                if self.cancelled():
                    return
                self._maximize(window)
            self._activate(snapshot.window)
            self._maximize(snapshot.window)

    @abstractmethod
    def _find_executable(self) -> Path | None: ...

    @abstractmethod
    def _launch(self, executable: str): ...

    @abstractmethod
    def _probe_locked(self) -> LoginSnapshot: ...

    @abstractmethod
    def _activate(self, window): ...

    @abstractmethod
    def _click(self, snapshot: LoginSnapshot): ...

    @abstractmethod
    def _standalone_windows(self): ...

    @abstractmethod
    def _maximize(self, window): ...
