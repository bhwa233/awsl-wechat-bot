"""微信 xlog 清理."""

import os
import platform
import threading
import time
from collections import deque
from datetime import date, datetime, timedelta
from pathlib import Path

from pydantic import BaseModel, Field

from daemon.metrics import metrics

WECHAT_LOG_CLEANUP_TIME = "03:30"


class WeChatLogCleanupRequest(BaseModel):
    dry_run: bool = True


class WeChatLogCleanupFile(BaseModel):
    path: str
    size: int
    mtime: str


class WeChatLogCleanupRecord(BaseModel):
    at: str
    source: str
    dry_run: bool
    message: str
    candidate_count: int
    candidate_bytes: int
    deleted_count: int
    deleted_bytes: int
    count: int
    bytes: int
    error_count: int


class WeChatLogCleanupResult(BaseModel):
    ok: bool = True
    dry_run: bool
    log_dir: str
    candidate_count: int = 0
    candidate_bytes: int = 0
    deleted_count: int = 0
    deleted_bytes: int = 0
    kept_count: int = 0
    kept_bytes: int = 0
    errors: list[str] = Field(default_factory=list)
    files: list[WeChatLogCleanupFile] = Field(default_factory=list)
    message: str = ""


class WeChatLogCleanupStatus(BaseModel):
    supported: bool
    log_dir: str
    exists: bool
    schedule_time: str
    next_run_at: str
    today: str
    recent_logs: list[WeChatLogCleanupRecord] = Field(default_factory=list)


class WeChatLogCleanupService:
    def __init__(self):
        self.log_dir = self._default_log_dir()
        self._records: deque[WeChatLogCleanupRecord] = deque(maxlen=20)
        self._last_schedule_date: date | None = None
        self._lock = threading.Lock()
        self._cleanup_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._schedule_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=3)

    def status(self) -> WeChatLogCleanupStatus:
        with self._lock:
            records = list(self._records)
        return WeChatLogCleanupStatus(
            supported=bool(self.log_dir),
            log_dir=str(self.log_dir) if self.log_dir else "",
            exists=bool(self.log_dir and self.log_dir.exists()),
            schedule_time=WECHAT_LOG_CLEANUP_TIME,
            next_run_at=self._next_run_at(),
            today=date.today().isoformat(),
            recent_logs=records,
        )

    def cleanup(self, dry_run: bool = True, source: str = "manual") -> WeChatLogCleanupResult:
        started = time.perf_counter()
        with self._cleanup_lock:
            result = self._scan_and_delete(dry_run)
            self._record(source, result)
            metrics.increment(
                "cleanup_runs_total",
                source=source,
                dry_run=str(dry_run).lower(),
                result="success" if result.ok else "failed",
            )
            metrics.observe("cleanup_duration_seconds", time.perf_counter() - started)
            if not dry_run:
                metrics.increment("cleanup_files_deleted_total", result.deleted_count)
                metrics.increment("cleanup_bytes_deleted_total", result.deleted_bytes)
            return result

    def _scan_and_delete(self, dry_run: bool) -> WeChatLogCleanupResult:
        result = WeChatLogCleanupResult(
            dry_run=dry_run,
            log_dir=str(self.log_dir) if self.log_dir else "",
        )
        if not self.log_dir:
            return self._finish(result, "当前平台未配置微信日志目录", ok=False)
        if not self.log_dir.exists():
            return self._finish(result, "微信日志目录不存在", ok=False)

        cutoff = datetime.combine(date.today(), datetime.min.time()).timestamp()
        for path in self.log_dir.rglob("*.xlog"):
            if not path.is_file():
                continue
            try:
                stat = path.stat()
            except OSError as exc:
                result.errors.append(f"{path}: {exc}")
                continue

            if stat.st_mtime >= cutoff:
                result.kept_count += 1
                result.kept_bytes += stat.st_size
                continue

            result.candidate_count += 1
            result.candidate_bytes += stat.st_size
            if len(result.files) < 200:
                result.files.append(WeChatLogCleanupFile(
                    path=str(path),
                    size=stat.st_size,
                    mtime=datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
                ))
            if dry_run:
                continue
            try:
                path.unlink()
                result.deleted_count += 1
                result.deleted_bytes += stat.st_size
            except OSError as exc:
                result.errors.append(f"{path}: {exc}")

        action = "可清理" if dry_run else "已清理"
        count = result.candidate_count if dry_run else result.deleted_count
        size = result.candidate_bytes if dry_run else result.deleted_bytes
        message = f"{action} {count} 个 xlog 文件, {self._format_bytes(size)}"
        if result.errors:
            message += f", 失败 {len(result.errors)} 个"
        return self._finish(result, message, ok=not result.errors)

    def _finish(self, result: WeChatLogCleanupResult, message: str, ok: bool) -> WeChatLogCleanupResult:
        result.ok = ok
        result.message = message
        if not ok and message not in result.errors:
            result.errors.append(message)
        return result

    def _schedule_loop(self) -> None:
        while not self._stop_event.is_set():
            self._run_due_schedule()
            self._stop_event.wait(timeout=30)

    def _run_due_schedule(self) -> None:
        now = datetime.now()
        if now < self._today_schedule_time(now):
            return
        with self._lock:
            if self._last_schedule_date == now.date():
                return
            self._last_schedule_date = now.date()
        self.cleanup(dry_run=False, source="scheduled")

    def _today_schedule_time(self, now: datetime) -> datetime:
        hour, minute = [int(part) for part in WECHAT_LOG_CLEANUP_TIME.split(":", 1)]
        return now.replace(hour=hour, minute=minute, second=0, microsecond=0)

    def _record(self, source: str, result: WeChatLogCleanupResult) -> None:
        count = result.candidate_count if result.dry_run else result.deleted_count
        size = result.candidate_bytes if result.dry_run else result.deleted_bytes
        with self._lock:
            self._records.appendleft(WeChatLogCleanupRecord(
                at=datetime.now().isoformat(timespec="seconds"),
                source=source,
                dry_run=result.dry_run,
                message=result.message,
                candidate_count=result.candidate_count,
                candidate_bytes=result.candidate_bytes,
                deleted_count=result.deleted_count,
                deleted_bytes=result.deleted_bytes,
                count=count,
                bytes=size,
                error_count=len(result.errors),
            ))

    def _next_run_at(self) -> str:
        now = datetime.now()
        next_run = self._today_schedule_time(now)
        if next_run <= now:
            next_run += timedelta(days=1)
        return next_run.isoformat(timespec="seconds")

    @staticmethod
    def _default_log_dir() -> Path | None:
        system = platform.system().lower()
        if system == "windows":
            appdata = os.environ.get("APPDATA")
            base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
            return base / "Tencent" / "xwechat" / "log"
        if system == "linux":
            return Path.home() / ".xwechat" / "log"
        return None

    @staticmethod
    def _format_bytes(value: int) -> str:
        if value >= 1024 * 1024 * 1024:
            return f"{value / 1024 / 1024 / 1024:.2f} GB"
        if value >= 1024 * 1024:
            return f"{value / 1024 / 1024:.2f} MB"
        if value >= 1024:
            return f"{value / 1024:.1f} KB"
        return f"{value} B"
