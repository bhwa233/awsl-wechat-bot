"""分离历史、实时和近期重扫游标,轮转处理各群,避免历史积压饿死增量。"""

import logging
import random
import time

from archive.client import failure_message

logger = logging.getLogger(__name__)


class ArchiveSync:
    def __init__(self, store, chatlog, cloud, config):
        self.store, self.chatlog, self.cloud, self.config = store, chatlog, cloud, config
        self._next_upload = 0
        self._upload_failures = 0

    def upload(self):
        if time.monotonic() < self._next_upload:
            return False
        try:
            batch = self.store.pending_batch(self.config.ARCHIVE_PAGE_SIZE)
            if batch:
                body, queued = batch
                self.cloud.upload(body)
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
        self.store.heartbeat()
        self.upload()
        for group in self.store.groups(enabled_only=True):
            if stop.is_set():
                break
            self.store.heartbeat()
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
                        return
                    cursor = self.store.cursor(talker, kind)
                    if cursor:
                        rows = self.chatlog.get_archive_messages(
                            talker, cursor["seq"], limit=self.config.ARCHIVE_PAGE_SIZE, offset=cursor["offset"],
                        )
                        self.store.stage_page(cursor, rows, self.config.ARCHIVE_PAGE_SIZE, self.config.ARCHIVE_MAX_PENDING_BYTES)
            except Exception as error:
                self.store.error(failure_message(error), talker)
                logger.warning("群归档读取未完成,保留游标(%s)", type(error).__name__)
            self.upload()
        self.store.heartbeat()
