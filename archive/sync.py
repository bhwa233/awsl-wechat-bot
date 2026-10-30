"""分离历史、实时和近期重扫游标,轮转处理各群,避免历史积压饿死增量。"""

import logging
import random
import sys
import time
import uuid

from archive.client import failure_message

logger = logging.getLogger(__name__)


class ArchiveSync:
    def __init__(self, store, chatlog, cloud, config):
        self.store, self.chatlog, self.cloud, self.config = store, chatlog, cloud, config
        self._next_upload = 0
        self._upload_failures = 0
        self._run = None
        self._did_work = False
        self._last_report = 0
        self._allowed_groups = None
        self._scope_checked_at = 0

    def upload(self):
        if time.monotonic() < self._next_upload:
            return False
        try:
            batch = self.store.pending_batch(self.config.ARCHIVE_PAGE_SIZE)
            if batch:
                self._did_work = True
                body, queued = batch
                self.cloud.upload({**body, **({"run_id": self._run["id"]} if self._run else {})})
                self.store.acknowledge(queued)
            self.store.error("")
            self._upload_failures = 0
            return True
        except Exception as error:
            self._upload_failures = min(self._upload_failures + 1, 6)
            self._next_upload = time.monotonic() + random.uniform(0.8, 1.2) * min(300, 5 * 2**self._upload_failures)
            self.store.error(failure_message(error))
            logger.warning("云端归档上传未完成,保留队列(%s)", type(error).__name__)
            return False

    def tick(self, stop):
        self._run = {"id": str(uuid.uuid4()), "started_at": int(time.time() * 1000)}
        self._did_work = False
        self.store.heartbeat()
        self.report()
        self.upload()
        for group in self.store.groups(enabled_only=True):
            if stop.is_set():
                break
            self.store.heartbeat()
            if time.monotonic() - self._last_report >= 30:
                self.report(running=True)
            talker = group["talker"]
            try:
                if not group["initialized"]:
                    latest = self.chatlog.get_archive_latest(talker)
                    cutoff = max((row["seq"] for row in latest), default=0)
                    self.store.initialize(talker, cutoff)
                if time.time() - group["last_reconcile"] >= self.config.ARCHIVE_RECONCILE_SECONDS:
                    live_cursor = self.store.cursor(talker, "live")
                    if live_cursor:
                        self.store.start_reconcile(
                            talker, max(0, int((time.time() - self.config.ARCHIVE_OVERLAP_SECONDS) * 1000) - 10_000), live_cursor["seq"],
                        )
                for kind in ("live", "history", "repair"):
                    if stop.is_set():
                        break
                    cursor = self.store.cursor(talker, kind)
                    if cursor:
                        rows = self.chatlog.get_archive_messages(
                            talker, cursor["seq"], limit=self.config.ARCHIVE_PAGE_SIZE, offset=cursor["offset"],
                        )
                        self._did_work |= bool(rows)
                        self.store.stage_page(cursor, rows, self.config.ARCHIVE_PAGE_SIZE, self.config.ARCHIVE_MAX_PENDING_BYTES)
            except Exception as error:
                self._did_work = True
                self.store.error(failure_message(error), talker)
                logger.warning("群归档读取未完成,保留游标(%s)", type(error).__name__)
            self.upload()
        self.store.heartbeat()
        self.report(finished=True)
        self._run = None

    def report(self, *, running=False, finished=False):
        self._last_report = time.monotonic()
        try:
            snapshot = self.store.status()
            if self._allowed_groups is None or time.monotonic() - self._scope_checked_at > 900:
                scope = self.cloud.scope()
                if scope.get("account_id") != self.store.account_id or scope.get("source_id") != self.store.source_id:
                    raise ValueError("本地归档身份与服务端不匹配")
                self._allowed_groups = set(scope["groups"])
                self._scope_checked_at = time.monotonic()
            groups = [{
                "talker": row["talker"], "display_name": row["display_name"], "enabled": bool(row["enabled"]),
                "history_done": bool(row["history_done"]), "pending": row["pending"], "last_read": row["last_read"],
                "last_upload": row["last_upload"], "error_code": "read_failed" if row["last_error"] else None,
            } for row in snapshot["groups"] if row["talker"] in self._allowed_groups]
            error = "upload_failed" if snapshot["last_error"] else None
            if not error and any(row["last_error"] for row in snapshot["groups"]):
                error = "read_failed"
            now = int(time.time() * 1000)
            body = {"account_id": self.store.account_id, "source_id": self.store.source_id,
                    "reported_at": now, "platform": "windows" if sys.platform == "win32" else "linux",
                    "poll_seconds": min(3600, max(1, int(self.config.ARCHIVE_POLL_SECONDS))),
                    "pending": snapshot["pending"], "pending_bytes": snapshot["pending_bytes"], "error_code": error}
            if self._run and (self._did_work or (running and snapshot["pending"]) or (finished and error)):
                body["run"] = {**self._run, "status": ("failed" if error else "partial" if snapshot["pending"] else "completed")
                               if finished else "running", "completed_at": now if finished else None, "error_code": error}
            for offset in range(0, max(1, len(groups)), 200):
                self.cloud.heartbeat({**body, "groups": groups[offset:offset + 200]})
        except Exception as error:
            self._allowed_groups = None
            # 监控上报失败不能删除队列或停止归档;远端保留上一次报告及其时间。
            logger.warning("归档监控上报未完成(%s)", type(error).__name__)
