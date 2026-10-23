"""统一的微信启动登录、进程看护和独立窗口恢复。"""
import logging
import sys
import threading
import time

from common.config import config
from gateway.startup.adapters import get_login_adapter
from gateway.startup.restart_budget import RestartBudget

logger = logging.getLogger(__name__)


class WeChatStartup:
    def __init__(self, adapter, console):
        self.adapter = adapter
        self.console = console
        self._stop = threading.Event()
        self._thread = None
        self._start_lock = threading.Lock()
        self._restart_budget = RestartBudget()
        self.supported = console is not None and sys.platform in {"win32", "linux"}
        self._status = {"state": "disabled", "message": ""}

    @property
    def status(self):
        return {**self._status, "enabled": config.WECHAT_AUTO_LOGIN_ENABLED, "supported": self.supported}

    def _set(self, state, message=""):
        self._status = {"state": state, "message": message}
        logger.info("微信自动启动: %s %s", state, message)

    def start(self, *, manual=False):
        if not manual and not config.WECHAT_AUTO_LOGIN_ENABLED:
            return False
        with self._start_lock:
            if not self.supported:
                self._set("unsupported", "当前平台不支持自动登录")
                return False
            if self._thread and self._thread.is_alive():
                return False
            self._stop.clear()
            self._set("starting")
            self._thread = threading.Thread(target=self._run, name="wechat-startup", daemon=True)
            self._thread.start()
            return True

    def stop(self):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _run(self):
        try:
            driver = get_login_adapter(self.adapter, self.console, cancelled=self._stop.is_set)
            while not self._stop.is_set():
                if not self._wait_for_session(driver):
                    return
                self._set("starting")
                driver.launch()
                result = self._login_and_restore(driver)
                if result == "ready":
                    result = "restart" if self._watch_loop(driver) else None
                if result != "restart" or self._stop.is_set():
                    return
                if not self._restart_budget.claim():
                    self._set("restart_limited", "一小时自动恢复已达 3 次,停止看护并保留窗口缓存")
                    return
                self._set("restarting", "微信已退出,5 秒后重新启动并恢复窗口")
                if self._stop.wait(5):
                    return
        except Exception as exc:
            if self._stop.is_set():
                return
            logger.exception("微信启动看护失败")
            self._set("failed", str(exc))

    def _wait_for_session(self, driver):
        while not self._stop.is_set():
            if driver.session_ready():
                return True
            if self._status["state"] != "waiting":
                self._set("waiting", "等待图形桌面就绪")
            if self._stop.wait(5):
                return False
        return False

    def _login_and_restore(self, driver):
        deadline = time.monotonic() + 120
        clicked = False
        stable = False
        while not self._stop.is_set():
            if not driver.processes() and time.monotonic() >= deadline - 110:
                return "restart"
            try:
                snapshot = driver.probe()
            except Exception as exc:
                if time.monotonic() >= deadline:
                    raise RuntimeError("等待微信登录界面超时") from exc
                logger.debug("等待微信登录界面: %s", exc)
                if self._stop.wait(2):
                    return
                stable = False
                continue
            if snapshot.state in {"qr_required", "locked"}:
                self._set(snapshot.state, "需要人工处理,停止自动登录和窗口恢复")
                return
            if snapshot.state == "login_button" and not clicked:
                if self._stop.is_set():
                    return
                result = driver.click_login()
                if result == "clicked":
                    clicked = True
                    self._set("logging_in")
                if result in {"qr_required", "locked"}:
                    self._set(result, "需要人工处理,停止自动登录和窗口恢复")
                    return
            if snapshot.state == "logged_in" and stable:
                if self._stop.is_set():
                    return
                cached = self.console.get_cached_standalone_windows()
                if not cached.get("available"):
                    raise RuntimeError(cached.get("message") or "读取窗口缓存失败")
                if cached.get("exists") and cached.get("windows"):
                    self._set("restoring")
                    restored = self.console.open_cached_standalone_windows()
                    if not restored.get("available"):
                        if time.monotonic() >= deadline:
                            raise RuntimeError(restored.get("message") or "恢复窗口失败")
                        if self._stop.wait(5):
                            return
                        continue
                driver.maximize_windows()
                self._set("ready")
                return "ready"
            stable = snapshot.state == "logged_in"
            if time.monotonic() >= deadline:
                self._set("timeout", "120 秒内未确认登录,不再自动操作")
                return
            if self._stop.wait(2):
                return

    def _watch_loop(self, driver):
        next_probe = 0.0
        while not self._stop.is_set():
            if not driver.processes():
                if self._stop.wait(2):
                    return False
                if not driver.processes():
                    return True
            now = time.monotonic()
            if now >= next_probe:
                next_probe = now + 30
                try:
                    snapshot = driver.probe()
                except Exception as exc:
                    logger.debug("暂缓微信状态检查: %s", exc)
                    if self._stop.wait(5):
                        return False
                    continue
                if snapshot.state in {"qr_required", "locked"}:
                    self._set(snapshot.state, "需要人工处理,停止看护并保留窗口缓存")
                    return False
                if snapshot.state == "login_button":
                    return True
                if snapshot.state == "logged_in":
                    if self._status["state"] != "ready":
                        self._set("ready")
                else:
                    self._set("waiting", "微信进程仍在运行,等待可识别界面; 保留窗口缓存")
                    next_probe = now + 5
            if self._stop.wait(5):
                return False
        return False
