"""ttyd 进程管理器"""

import logging
import os
import shutil
import subprocess
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from common.config import config

logger = logging.getLogger("daemon")

PROJECT_DIR = Path(__file__).parent.parent.resolve()


class TtydProcessManager:
    def __init__(self):
        self._process: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()
        self._start_time: Optional[datetime] = None
        self._last_start_reason = ""
        self._shell_command = ""

    def start_ttyd(self, reason: str = "manual") -> dict:
        with self._lock:
            if self._is_running():
                logger.warning("start_ttyd 被调用但 ttyd 已在运行中")
                return {"ok": False, "message": "ttyd 已在运行中"}
            return self._launch(reason)

    def install_ttyd(self) -> dict:
        with self._lock:
            return self._install_ttyd()

    def stop_ttyd(self) -> dict:
        with self._lock:
            result = self._terminate()
            logger.info(f"ttyd 已停止: {result['message']}")
            return result

    def restart_ttyd(self, reason: str = "manual") -> dict:
        with self._lock:
            self._terminate()
            return self._launch(reason)

    def get_status(self) -> dict:
        with self._lock:
            return self._status_payload(self._is_running())

    def shutdown(self):
        with self._lock:
            self._terminate()

    def _launch(self, reason: str) -> dict:
        ttyd_bin, install_error = self._ensure_ttyd_bin()
        if install_error:
            return install_error
        if not (shell_name := self._resolve_shell_name()):
            return self._shell_not_found_error()

        command = self._build_command(ttyd_bin, shell_name)
        try:
            self._process = subprocess.Popen(command, **self._popen_kwargs())
        except OSError as exc:
            logger.exception("启动 ttyd 失败")
            return {"ok": False, "message": f"启动 ttyd 失败: {exc}"}

        self._start_pipe_reader()
        self._start_time = datetime.now(timezone.utc)
        self._last_start_reason = reason
        self._shell_command = shell_name
        logger.info(
            "ttyd 已启动 (pid=%s, host=%s, port=%s, shell=%s, auth=%s, reason=%s)",
            self._process.pid,
            config.TTYD_HOST,
            config.TTYD_PORT,
            shell_name,
            bool(config.TTYD_CREDENTIALS),
            reason,
        )
        return {
            "ok": True,
            "message": "ttyd 已启动",
            "pid": self._process.pid,
            "host": config.TTYD_HOST,
            "port": config.TTYD_PORT,
            "shell": shell_name,
            "auth_enabled": bool(config.TTYD_CREDENTIALS),
        }

    def _pipe_reader(self, process: subprocess.Popen):
        try:
            for line in iter(process.stdout.readline, b""):
                decoded = self._decode_line(line)
                decoded and logger.info(f"[ttyd] {decoded}")
        finally:
            try:
                process.stdout and process.stdout.close()
            except Exception:
                pass

    def _terminate(self) -> dict:
        if not self._process:
            return {"ok": True, "message": "ttyd 未在运行"}
        pid = self._process.pid
        try:
            stop_process = self._stop_win32 if sys.platform == "win32" else self._stop_posix
            stop_process()
        except Exception:
            logger.exception("停止 ttyd 失败")
        self._process = None
        self._start_time = None
        return {"ok": True, "message": f"ttyd 已停止 (pid={pid})"}

    def _resolve_shell_name(self) -> str:
        return next((name for name in ("pwsh", "powershell") if shutil.which(name)), "")

    def _is_running(self) -> bool:
        return bool(self._process and self._process.poll() is None)

    def _status_payload(self, running: bool) -> dict:
        return {
            "running": running,
            "installed": bool(shutil.which("ttyd")),
            "auth_enabled": bool(config.TTYD_CREDENTIALS),
            "pid": self._process.pid if running else None,
            "uptime_seconds": (
                (datetime.now(timezone.utc) - self._start_time).total_seconds()
                if running and self._start_time
                else None
            ),
            "host": config.TTYD_HOST,
            "port": config.TTYD_PORT,
            "shell": self._shell_command or self._resolve_shell_name(),
            "last_start_reason": self._last_start_reason,
        }

    def _find_ttyd_bin(self) -> str:
        return shutil.which("ttyd") or ""

    def _ensure_ttyd_bin(self) -> tuple[str, Optional[dict]]:
        if ttyd_bin := self._find_ttyd_bin():
            return ttyd_bin, None
        install_result = self._install_ttyd()
        if not install_result["ok"]:
            return "", install_result
        if ttyd_bin := self._find_ttyd_bin():
            return ttyd_bin, None
        return "", self._ttyd_not_found_after_install_error()

    def _build_command(self, ttyd_bin: str, shell_name: str) -> list[str]:
        return [
            ttyd_bin,
            "-i",
            config.TTYD_HOST,
            "-p",
            str(config.TTYD_PORT),
            *(["-c", config.TTYD_CREDENTIALS] if config.TTYD_CREDENTIALS else []),
            shell_name,
        ]

    def _start_pipe_reader(self):
        threading.Thread(
            target=self._pipe_reader,
            args=(self._process,),
            daemon=True,
            name="ttyd-log-reader",
        ).start()

    def _popen_kwargs(self) -> dict:
        kwargs = {
            "stdout": subprocess.PIPE,
            "stderr": subprocess.STDOUT,
            "cwd": str(PROJECT_DIR),
            "env": os.environ.copy(),
        }
        if sys.platform == "win32":
            kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
        return kwargs

    def _stop_win32(self):
        subprocess.run(
            ["taskkill", "/PID", str(self._process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
        self._process.wait(timeout=3)

    def _stop_posix(self):
        self._process.terminate()
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=3)

    def _decode_line(self, line: bytes) -> str:
        try:
            return line.decode("utf-8", errors="replace").rstrip()
        except Exception:
            return ""

    def _shell_not_found_error(self) -> dict:
        logger.error("未找到 pwsh 或 powershell")
        return {
            "ok": False,
            "message": "未找到 pwsh 或 powershell，请确认 PowerShell 已安装且在 PATH 中",
        }

    def _ttyd_not_found_after_install_error(self) -> dict:
        logger.error("winget 安装完成后仍未找到 ttyd")
        return {
            "ok": False,
            "message": "已执行 winget 安装，但当前进程仍未找到 ttyd，请重启 daemon 后再试",
        }

    def _install_ttyd(self) -> dict:
        if self._find_ttyd_bin():
            return {"ok": True, "message": "ttyd 已安装"}
        if sys.platform != "win32":
            return {
                "ok": False,
                "message": "当前平台不支持通过 winget 自动安装 ttyd",
            }
        winget_bin = shutil.which("winget") or ""
        if not winget_bin:
            return {"ok": False, "message": "未找到 winget，无法自动安装 ttyd"}

        logger.info("开始通过 winget 安装 ttyd")
        try:
            result = subprocess.run(
                [
                    winget_bin,
                    "install",
                    "--id",
                    "tsl0922.ttyd",
                    "-e",
                    "--source",
                    "winget",
                    "--accept-source-agreements",
                    "--accept-package-agreements",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=600,
            )
        except Exception as exc:
            logger.exception("winget 安装 ttyd 失败")
            return {"ok": False, "message": f"winget 安装 ttyd 失败: {exc}"}

        if result.returncode == 0 or self._find_ttyd_bin():
            logger.info("winget 安装 ttyd 完成")
            return {"ok": True, "message": "ttyd 已安装"}

        detail = (
            (result.stderr or "").strip()
            or (result.stdout or "").strip()
            or f"exit_code={result.returncode}"
        )
        return {"ok": False, "message": f"winget 安装 ttyd 失败: {detail}"}
