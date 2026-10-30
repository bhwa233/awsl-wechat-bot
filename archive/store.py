"""持久化待上传队列及每群游标;页面入队与游标推进处于同一事务。"""

import json
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from archive.models import MAX_BATCH_BYTES, ArchiveMessage, digest, encode
from common.sqlite_migrator import SQLiteMigrator


def _create_schema(conn):
    for sql in (
        "CREATE TABLE identity (account_id TEXT NOT NULL, source_id TEXT NOT NULL)",
        """CREATE TABLE groups (
            talker TEXT PRIMARY KEY, display_name TEXT NOT NULL, enabled INTEGER NOT NULL DEFAULT 1,
            initialized INTEGER NOT NULL DEFAULT 0, history_done INTEGER NOT NULL DEFAULT 0,
            history_cutoff INTEGER NOT NULL DEFAULT 0, last_reconcile INTEGER NOT NULL DEFAULT 0,
            last_read INTEGER, last_upload INTEGER, last_error TEXT NOT NULL DEFAULT '')""",
        """CREATE TABLE cursors (
            talker TEXT NOT NULL, kind TEXT NOT NULL, seq INTEGER NOT NULL, offset INTEGER NOT NULL DEFAULT 0,
            cutoff INTEGER, PRIMARY KEY(talker, kind))""",
        """CREATE TABLE seen (
            talker TEXT NOT NULL, local_key TEXT NOT NULL, digest TEXT NOT NULL, PRIMARY KEY(talker, local_key))""",
        """CREATE TABLE outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT, talker TEXT NOT NULL, local_key TEXT NOT NULL,
            payload TEXT NOT NULL, bytes INTEGER NOT NULL, UNIQUE(talker, local_key))""",
        "CREATE TABLE runtime (id INTEGER PRIMARY KEY CHECK(id=1), heartbeat INTEGER, last_error TEXT NOT NULL DEFAULT '')",
        "INSERT INTO runtime(id) VALUES(1)",
    ):
        conn.execute(sql)


class ArchiveStore:
    def __init__(self, path: str | Path, account_id: str, source_id: str):
        self.path = str(path)
        self.account_id, self.source_id = account_id, source_id
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).touch(mode=0o600, exist_ok=True)
        SQLiteMigrator(self.path).migrate([("001_archive", _create_schema)])
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT account_id, source_id FROM identity").fetchone()
            if row and tuple(row) != (account_id, source_id):
                raise ValueError("归档状态文件属于其他账号或设备,请使用独立的 ARCHIVE_STATE_PATH")
            if not row:
                conn.execute("INSERT INTO identity VALUES (?, ?)", (account_id, source_id))

    def connect(self):
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def groups(self, *, enabled_only=False):
        with closing(self.connect()) as conn:
            return [dict(row) for row in conn.execute(
                "SELECT g.*, (SELECT COUNT(*) FROM outbox o WHERE o.talker=g.talker) AS pending "
                "FROM groups g " + ("WHERE enabled=1 " if enabled_only else "") + "ORDER BY talker",
            )]

    def set_group(self, talker: str, display_name: str, enabled: bool):
        with closing(self.connect()) as conn, conn:
            conn.execute(
                "INSERT INTO groups(talker, display_name, enabled) VALUES (?, ?, ?) "
                "ON CONFLICT(talker) DO UPDATE SET display_name=excluded.display_name, enabled=excluded.enabled",
                (talker, display_name, int(enabled)),
            )

    def initialize(self, talker: str, cutoff: int):
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT initialized FROM groups WHERE talker=?", (talker,)).fetchone()
            if not row or row[0]:
                return
            conn.execute("UPDATE groups SET initialized=1, history_cutoff=? WHERE talker=?", (cutoff, talker))
            conn.execute("INSERT INTO cursors(talker, kind, seq) VALUES (?, 'live', ?)", (talker, cutoff))
            conn.execute("INSERT INTO cursors(talker, kind, seq, cutoff) VALUES (?, 'history', 0, ?)", (talker, cutoff))

    def cursor(self, talker: str, kind: str):
        with closing(self.connect()) as conn:
            row = conn.execute("SELECT * FROM cursors WHERE talker=? AND kind=?", (talker, kind)).fetchone()
            return dict(row) if row else None

    def start_reconcile(self, talker: str, since: int, cutoff: int):
        with closing(self.connect()) as conn, conn:
            conn.execute("INSERT OR IGNORE INTO cursors(talker, kind, seq, cutoff) VALUES (?, 'repair', ?, ?)",
                         (talker, since, cutoff))

    def replay(self, talker: str):
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM seen WHERE talker=?", (talker,))
            conn.execute("DELETE FROM cursors WHERE talker=? AND kind!='live'", (talker,))
            conn.execute("UPDATE groups SET initialized=0, history_done=0, last_error='' WHERE talker=?", (talker,))
            conn.execute("DELETE FROM cursors WHERE talker=? AND kind='live'", (talker,))

    def stage_page(self, cursor: dict, rows: list[dict], limit: int, max_pending_bytes: int):
        talker, kind = cursor["talker"], cursor["kind"]
        if any(row["talker"] != talker for row in rows):
            raise ValueError("Chatlog 返回了不属于请求群的消息")
        # 完整校验一页后才写入;无效/超大消息会保留旧游标,不能静默跳过。
        messages = [ArchiveMessage.from_chatlog(row).model_dump() for row in rows]
        if any(m["seq"] < cursor["seq"] for m in messages):
            raise ValueError("Chatlog 返回了游标之前的消息")
        if [(m["seq"], m["local_id"]) for m in messages] != sorted((m["seq"], m["local_id"]) for m in messages):
            raise ValueError("Chatlog 消息顺序无效")
        selected = [m for m in messages if cursor["cutoff"] is None or m["seq"] <= cursor["cutoff"]]
        now = int(time.time() * 1000)
        with closing(self.connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute("SELECT * FROM cursors WHERE talker=? AND kind=?", (talker, kind)).fetchone()
            enabled = conn.execute("SELECT enabled FROM groups WHERE talker=?", (talker,)).fetchone()
            if not current or dict(current) != cursor or not enabled or not enabled[0]:
                return  # 管理端暂停或重新补录后,丢弃已过期的读取结果。
            for message in selected:
                fingerprint = digest(message)
                # 跨数据库的 seq/local_id 可能重合;有服务端 ID 时本地队列也必须按正式身份区分。
                key = "s:" + message["server_id"] if message["server_id"] != "0" else "l:" + message["local_key"]
                previous = conn.execute("SELECT digest FROM seen WHERE talker=? AND local_key=?",
                                        (talker, key)).fetchone()
                if previous and previous[0] == fingerprint:
                    continue
                payload = encode({**message, "observed_at": now})
                size = len(payload.encode())
                envelope = {"account_id": self.account_id, "source_id": self.source_id, "batch_id": "0" * 64,
                            "groups": [{"talker": talker, "display_name": ""}], "messages": []}
                # 群名最多 1000 字符,为 UTF-8 编码保留 4000 字节及少量封装余量。
                if size + len(encode(envelope).encode()) + 4096 > MAX_BATCH_BYTES:
                    raise ValueError("单条消息超过归档上传限制;原文未丢弃,游标保持不变")
                conn.execute(
                    "INSERT INTO outbox(talker, local_key, payload, bytes) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(talker, local_key) DO UPDATE SET payload=excluded.payload, bytes=excluded.bytes",
                    (talker, key, payload, size),
                )
                conn.execute("INSERT INTO seen VALUES (?, ?, ?) ON CONFLICT(talker, local_key) DO UPDATE SET digest=excluded.digest",
                             (talker, key, fingerprint))
            if conn.execute("SELECT COALESCE(SUM(bytes), 0) FROM outbox").fetchone()[0] > max_pending_bytes:
                raise ValueError("待上传队列已达容量上限;恢复上传后自动继续读取")
            done = cursor["cutoff"] is not None and (len(messages) < limit or len(selected) < len(messages))
            if done:
                conn.execute("DELETE FROM cursors WHERE talker=? AND kind=?", (talker, kind))
                if kind == "history":
                    conn.execute("UPDATE groups SET history_done=1 WHERE talker=?", (talker,))
                else:
                    conn.execute("UPDATE groups SET last_reconcile=? WHERE talker=?", (int(time.time()), talker))
            elif selected:
                seq = selected[-1]["seq"]
                offset = sum(m["seq"] == seq for m in selected) + (cursor["offset"] if seq == cursor["seq"] else 0)
                conn.execute("UPDATE cursors SET seq=?, offset=? WHERE talker=? AND kind=?", (seq, offset, talker, kind))
            conn.execute("UPDATE groups SET last_read=?, last_error='' WHERE talker=?", (now, talker))

    def pending_batch(self, limit=200):
        with closing(self.connect()) as conn:
            rows = conn.execute(
                "SELECT o.*, g.display_name FROM outbox o JOIN groups g USING(talker) WHERE g.enabled=1 ORDER BY o.id LIMIT ?",
                (min(limit, 200),),
            ).fetchall()
        messages, groups, queued = [], {}, []
        body = {"account_id": self.account_id, "source_id": self.source_id, "groups": [], "messages": messages}
        for row in rows:
            message = json.loads(row["payload"])
            groups[row["talker"]] = row["display_name"]
            candidate = {**body, "groups": [{"talker": key, "display_name": name} for key, name in groups.items()],
                         "messages": [*messages, message], "batch_id": "0" * 64}
            if len(encode(candidate).encode()) > MAX_BATCH_BYTES:
                break
            messages.append(message)
            queued.append((row["id"], row["payload"], row["talker"]))
            body["groups"] = candidate["groups"]
        if not queued:
            return None
        body["batch_id"] = digest(body)
        return body, queued

    def acknowledge(self, queued):
        with closing(self.connect()) as conn, conn:
            for row_id, payload, talker in queued:
                conn.execute("DELETE FROM outbox WHERE id=? AND payload=?", (row_id, payload))
                conn.execute("UPDATE groups SET last_upload=? WHERE talker=?", (int(time.time() * 1000), talker))

    def error(self, message: str, talker: str | None = None):
        with closing(self.connect()) as conn, conn:
            if talker:
                conn.execute("UPDATE groups SET last_error=? WHERE talker=?", (message, talker))
            else:
                conn.execute("UPDATE runtime SET last_error=? WHERE id=1", (message,))

    def heartbeat(self):
        with closing(self.connect()) as conn, conn:
            conn.execute("UPDATE runtime SET heartbeat=? WHERE id=1", (int(time.time() * 1000),))

    def status(self):
        with closing(self.connect()) as conn:
            runtime = dict(conn.execute("SELECT heartbeat, last_error FROM runtime WHERE id=1").fetchone())
            pending = conn.execute("SELECT COUNT(*), COALESCE(SUM(bytes), 0) FROM outbox").fetchone()
        return {**runtime, "pending": pending[0], "pending_bytes": pending[1], "groups": self.groups()}
