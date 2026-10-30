import json
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import httpx

from archive.client import ArchiveClient
from archive.models import MAX_BATCH_BYTES, encode
from archive.store import ArchiveStore
from archive.sync import ArchiveSync
from cortex.services.chatlog_client import ChatlogClient


def message(local_id, *, seq=101, content="同一条正文", server_id=None):
    return {
        "talker": "room@chatroom", "seq": seq, "local_id": local_id,
        "server_id_str": str(server_id if server_id is not None else 9007199254740991000 + local_id),
        "create_time": 100, "sender": "member", "sender_name": "群成员", "msg_type": 1,
        "content": content, "is_self": local_id % 2 == 0, "at_users": [],
    }


class ArchiveSyncTest(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.path = Path(directory) / "state.db"
        self.store = ArchiveStore(self.path, "account", "windows")
        self.store.set_group("room@chatroom", "测试群", True)
        self.config = SimpleNamespace(ARCHIVE_PAGE_SIZE=2, ARCHIVE_MAX_PENDING_BYTES=10_000_000,
                                      ARCHIVE_RECONCILE_SECONDS=3600, ARCHIVE_OVERLAP_SECONDS=86400)

    def test_same_seq_pages_survive_restart_and_lost_ack_without_dropping_self_messages(self):
        rows = [message(1), message(1, server_id=9007199254740991008), *[message(i) for i in range(2, 8)]]
        received = {}
        lose_ack = True

        def chatlog(request):
            if request.method == "GET":
                return httpx.Response(200, json=[rows[-1]])
            body = json.loads(request.content)
            since, offset, limit = body["items"][0]["since_seq"], body["offset"], body["limit"]
            page = [row for row in rows if row["seq"] >= since][offset:offset + limit]
            return httpx.Response(200, json={"results": {"room@chatroom": page}})

        def cloud(request):
            nonlocal lose_ack
            body = json.loads(request.content)
            for row in body["messages"]:
                received[row["server_id"]] = row
            if lose_ack:
                lose_ack = False
                raise httpx.ReadTimeout("response lost", request=request)
            return httpx.Response(200, json={"batch_id": body["batch_id"], "accepted": len(body["messages"])})

        local = ChatlogClient("http://chatlog")
        self.addCleanup(local.close)
        local._client = httpx.Client(base_url="http://chatlog", transport=httpx.MockTransport(chatlog))
        remote = ArchiveClient("http://cloud", "not-used-by-transport")
        self.addCleanup(remote.close)
        remote.client = httpx.Client(base_url="http://cloud", transport=httpx.MockTransport(cloud))
        sync = ArchiveSync(self.store, local, remote, self.config)
        sync.tick(threading.Event())
        self.assertGreater(self.store.status()["pending"], 0)
        # 磁盘重开并重新创建同步器,不能依赖进程内的去重集合或分页状态。
        self.store = ArchiveStore(self.path, "account", "windows")
        sync = ArchiveSync(self.store, local, remote, self.config)
        for _ in range(6):
            sync.tick(threading.Event())
        self.assertEqual(set(received), {str(9007199254740991000 + i) for i in range(1, 9)})
        self.assertEqual(sum(row["is_self"] for row in received.values()), 3)
        self.assertEqual(self.store.status()["pending"], 0)
        self.assertTrue(self.store.groups()[0]["history_done"])

    def test_invalid_page_or_full_queue_rolls_back_seen_outbox_and_cursor_together(self):
        self.store.initialize("room@chatroom", 101)
        cursor = self.store.cursor("room@chatroom", "history")
        for rows, capacity in [([message(1), {**message(2), "server_id_str": "invalid"}], 10_000), ([message(1)], 1)]:
            with self.subTest(capacity=capacity), self.assertRaises(ValueError):
                self.store.stage_page(cursor, rows, 2, capacity)
            reopened = ArchiveStore(self.path, "account", "windows")
            self.assertEqual(reopened.cursor("room@chatroom", "history"), cursor)
            self.assertEqual(reopened.status()["pending"], 0)
            with closing(reopened.connect()) as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM seen").fetchone()[0], 0)

    def test_old_ack_cannot_delete_a_new_content_update_and_unchanged_rescan_is_quiet(self):
        self.store.initialize("room@chatroom", 101)
        self.store.stage_page(self.store.cursor("room@chatroom", "history"), [message(1)], 2, 10_000)
        old_body, old_ack = self.store.pending_batch()
        self.store.start_reconcile("room@chatroom", 0, 101)
        self.store.stage_page(self.store.cursor("room@chatroom", "repair"), [message(1, content="更正后的正文")], 2, 10_000)
        self.store.acknowledge(old_ack)
        body, ack = self.store.pending_batch()
        self.assertNotEqual(body["batch_id"], old_body["batch_id"])
        self.assertEqual(body["messages"][0]["content"], "更正后的正文")
        self.store.acknowledge(ack)
        self.store.start_reconcile("room@chatroom", 0, 101)
        self.store.stage_page(self.store.cursor("room@chatroom", "repair"), [message(1, content="更正后的正文")], 2, 10_000)
        self.assertIsNone(self.store.pending_batch())

    def test_utf8_batches_split_without_truncation_and_pausing_retains_queue(self):
        self.store.initialize("room@chatroom", 101)
        content = "聊" * 180_000
        self.store.stage_page(self.store.cursor("room@chatroom", "history"),
                              [message(1, content=content), message(2, content=content)], 2, 10_000_000)
        self.store.set_group("room@chatroom", "测试群", False)
        self.assertIsNone(self.store.pending_batch())
        self.assertEqual(self.store.status()["pending"], 2)
        self.store.set_group("room@chatroom", "测试群", True)
        recovered = []
        while batch := self.store.pending_batch():
            body, ack = batch
            self.assertLessEqual(len(encode(body).encode()), MAX_BATCH_BYTES)
            self.assertEqual(len(body["messages"]), 1)
            recovered.extend(row["content"] for row in body["messages"])
            self.store.acknowledge(ack)
        self.assertEqual(recovered, [content, content])

    def test_missing_group_in_batch_response_does_not_advance_progress(self):
        self.store.initialize("room@chatroom", 101)
        cursor = self.store.cursor("room@chatroom", "live")
        client = ChatlogClient("http://chatlog")
        self.addCleanup(client.close)
        client._client = httpx.Client(base_url="http://chatlog", transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"results": {}})))
        with self.assertRaises(ValueError):
            client.get_archive_messages("room@chatroom", cursor["seq"])
        self.assertEqual(self.store.cursor("room@chatroom", "live"), cursor)

    def test_state_identity_rejects_account_or_device_reuse(self):
        for account, source in [("other", "windows"), ("account", "linux")]:
            with self.subTest(account=account, source=source), self.assertRaises(ValueError):
                ArchiveStore(self.path, account, source)


if __name__ == "__main__":
    unittest.main()
