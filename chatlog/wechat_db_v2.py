"""微信数据库读取器 v2 - 通过 awsl-wechat-chatlog DLL 在线查询 SQLCipher v4

与 v1 的差别：
- 不离线解密落盘，DLL 直接打开密文 db
- 输入路径是 WECHAT_DB_INPUT_PATH（加密目录），子路径 db_storage/{message,contact}
- 复用 v1 的 ChatRoom/Message dataclass、_parse_room_data、zstd 解压逻辑
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import sys
from datetime import datetime
from typing import Any, Optional

from chatlog.dll_loader import query_raw_sql
from chatlog.wechat_db import (
    ChatRoom,
    HAS_ZSTD,
    MESSAGE_TYPE_TEXT,
    Message,
    _parse_room_data,
)

if HAS_ZSTD:
    import zstandard as zstd

logger = logging.getLogger(__name__)

_TABLE_NAME_RE = re.compile(r"^Msg_[0-9a-f]{32}$")
_MESSAGE_DB_RE = re.compile(r"^message_([0-9]?[0-9])?\.db$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_@.\-]{1,128}$")


def _validate_username(name: str) -> str:
    if not _USERNAME_RE.match(name):
        raise ValueError(f"非法的 username/talker: {name!r}")
    return name


def _talker_to_table_name(talker: str) -> str:
    return f"Msg_{hashlib.md5(talker.encode()).hexdigest()}"


def _decode_blob(value: Any) -> bytes | Any:
    """Go encoding/json 把 []byte 编码为 base64 字符串，对已知 BLOB 列调用此函数还原。
    只有解出来不是合法 UTF-8 文本时才认为是真正的 BLOB，避免误把纯文本 base64 化。"""
    if isinstance(value, bytes):
        return value
    if not isinstance(value, str):
        return value
    try:
        raw = base64.b64decode(value, validate=True)
    except Exception:
        return value
    try:
        raw.decode("utf-8")
        return value
    except UnicodeDecodeError:
        return raw


def _decompress_content(content: bytes | str | None) -> str:
    if not content:
        return ""
    if isinstance(content, str):
        return content
    if HAS_ZSTD and len(content) >= 4 and content[:4] == b"\x28\xb5\x2f\xfd":
        try:
            return zstd.ZstdDecompressor().decompress(content).decode("utf-8")
        except Exception:
            pass
    return content.decode("utf-8", errors="ignore")


class WeChatDBReaderV2:
    """v2 reader：直接读加密目录，DLL 跑 raw SQL"""

    def __init__(self, encrypted_dir: str, hex_key: str):
        self.encrypted_dir = encrypted_dir
        self.hex_key = hex_key
        self._message_db_times: list[tuple[str, int, int]] = []
        self._chatroom_display_names: dict[str, dict[str, str]] = {}

    def _contact_db_path(self) -> str:
        path = os.path.join(self.encrypted_dir, "db_storage", "contact", "contact.db")
        if not os.path.isfile(path):
            raise FileNotFoundError(f"联系人数据库不存在: {path}")
        return path

    def _list_message_db_paths(self) -> list[str]:
        message_dir = os.path.join(self.encrypted_dir, "db_storage", "message")
        if not os.path.isdir(message_dir):
            raise FileNotFoundError(f"消息数据库目录不存在: {message_dir}")
        files = [
            os.path.join(message_dir, f)
            for f in os.listdir(message_dir)
            if _MESSAGE_DB_RE.match(f)
        ]
        return sorted(files)

    def _query(self, db_path: str, sql: str) -> tuple[list[str], list[list]]:
        result = query_raw_sql(db_path, self.hex_key, sql)
        return result.get("columns", []) or [], result.get("rows", []) or []

    def _init_message_dbs(self) -> None:
        if self._message_db_times:
            return
        infos = []
        errors = []
        for path in self._list_message_db_paths():
            try:
                _, rows = self._query(path, "SELECT timestamp FROM Timestamp LIMIT 1")
                if rows and rows[0]:
                    infos.append((path, int(rows[0][0])))
            except Exception as e:
                errors.append((path, e))
                logger.warning("[ChatlogV2] 无法读取数据库 %s: %s", path, e)
        if not infos:
            detail = "; ".join(f"{p}: {e}" for p, e in errors) if errors else "目录下无 message db"
            raise RuntimeError(f"没有可用的消息数据库: {detail}")
        infos.sort(key=lambda x: x[1])
        for i, (path, start_time) in enumerate(infos):
            end_time = infos[i + 1][1] if i < len(infos) - 1 else sys.maxsize
            self._message_db_times.append((path, start_time, end_time))

    def _get_dbs_for_time_range(self, start_ts: int, end_ts: int) -> list[str]:
        self._init_message_dbs()
        return [p for p, s, e in self._message_db_times if s < end_ts and e > start_ts]

    def list_groups(self, limit: int = 0) -> list[ChatRoom]:
        contact_db = self._contact_db_path()
        sql = (
            "SELECT c.username, COALESCE(cr.owner, ''), c.nick_name, c.remark "
            "FROM contact c "
            "LEFT JOIN chat_room cr ON c.username = cr.username "
            "WHERE c.local_type IN (1, 2) "
            "AND c.username NOT LIKE 'gh_%' "
            "AND c.username NOT IN "
            "('filehelper','fmessage','floatbottle','medianote','newsapp','weixin','weixinpay') "
            "AND c.delete_flag = 0 "
            "ORDER BY c.local_type, c.remark, c.nick_name"
        )
        if limit > 0:
            sql += f" LIMIT {int(limit)}"
        _, rows = self._query(contact_db, sql)
        return [
            ChatRoom(
                username=row[0] or "",
                owner=row[1] or "",
                nick_name=row[2] or "",
                remark=row[3] or "",
            )
            for row in rows
        ]

    def get_group_display_name(self, group_id: str) -> str:
        _validate_username(group_id)
        contact_db = self._contact_db_path()
        _, rows = self._query(
            contact_db,
            f"SELECT nick_name, remark FROM contact WHERE username='{group_id}' LIMIT 1",
        )
        if rows:
            return rows[0][1] or rows[0][0] or group_id
        return group_id

    def _batch_get_contact_names(self, usernames: set[str]) -> dict[str, str]:
        if not usernames:
            return {}
        safe = []
        for u in usernames:
            try:
                _validate_username(u)
                safe.append(u)
            except ValueError:
                continue
        if not safe:
            return {}
        in_clause = ",".join(f"'{u}'" for u in safe)
        contact_db = self._contact_db_path()
        _, rows = self._query(
            contact_db,
            f"SELECT username, nick_name, remark, alias FROM contact WHERE username IN ({in_clause})",
        )
        result: dict[str, str] = {}
        for row in rows:
            username = row[0] or ""
            name = row[2] or row[1] or row[3] or username
            result[username] = name
        return result

    def _get_chatroom_display_names(self, chatroom: str) -> dict[str, str]:
        if chatroom in self._chatroom_display_names:
            return self._chatroom_display_names[chatroom]
        names: dict[str, str] = {}
        try:
            _validate_username(chatroom)
            contact_db = self._contact_db_path()
            _, rows = self._query(
                contact_db,
                f"SELECT ext_buffer FROM chat_room WHERE username='{chatroom}' LIMIT 1",
            )
            if rows and rows[0][0]:
                ext_buffer = _decode_blob(rows[0][0])
                if isinstance(ext_buffer, str):
                    ext_buffer = ext_buffer.encode("latin-1", errors="ignore")
                names = _parse_room_data(ext_buffer)
        except Exception:
            return names
        self._chatroom_display_names[chatroom] = names
        return names

    def get_messages(
        self,
        talker: str,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        text_only: bool = True,
        limit: int = 0,
        offset: int = 0,
        desc: bool = False,
    ) -> list[Message]:
        _validate_username(talker)
        start_time = start_time or datetime(2000, 1, 1)
        end_time = end_time or datetime.now()
        start_ts, end_ts = int(start_time.timestamp()), int(end_time.timestamp())

        db_paths = self._get_dbs_for_time_range(start_ts, end_ts)
        if not db_paths:
            return []

        table_name = _talker_to_table_name(talker)
        if not _TABLE_NAME_RE.match(table_name):
            raise ValueError(f"Invalid table name derived from talker: {talker}")
        is_chatroom = talker.endswith("@chatroom")
        display_names = self._get_chatroom_display_names(talker) if is_chatroom else {}

        type_filter = f" AND m.local_type = {MESSAGE_TYPE_TEXT}" if text_only else ""
        sql_limit = ""
        if limit > 0:
            sql_limit = f" LIMIT {int(offset + limit)}"

        raw_messages: list[tuple] = []
        for db_path in db_paths:
            try:
                _, exists_rows = self._query(
                    db_path,
                    f"SELECT 1 FROM sqlite_master WHERE type='table' AND name='{table_name}' LIMIT 1",
                )
                if not exists_rows:
                    continue
                sql = (
                    f"SELECT m.sort_seq, m.server_id, m.local_type, n.user_name, "
                    f"m.create_time, m.message_content, m.status "
                    f"FROM {table_name} m "
                    f"LEFT JOIN Name2Id n ON m.real_sender_id = n.rowid "
                    f"WHERE m.create_time >= {start_ts} AND m.create_time <= {end_ts}"
                    f"{type_filter} "
                    f"ORDER BY m.sort_seq {'DESC' if desc else 'ASC'}{sql_limit}"
                )
                _, rows = self._query(db_path, sql)
            except Exception as e:
                logger.warning("[ChatlogV2] 查询 %s 失败: %s", db_path, e)
                continue

            for row in rows:
                sort_seq, _server_id, local_type, user_name, create_time, message_content, status = row
                msg_type = int(local_type) & 0xFFFFFFFF

                content = _decompress_content(_decode_blob(message_content))
                sender = user_name or ""
                if is_chatroom and ":\n" in content:
                    parts = content.split(":\n", 1)
                    if len(parts) == 2 and " " not in parts[0] and "\n" not in parts[0]:
                        sender, content = parts

                raw_messages.append((
                    int(sort_seq), talker, sender, msg_type, content,
                    int(create_time), status == 2 or (not is_chatroom and talker != sender),
                ))

        raw_messages.sort(key=lambda m: m[0], reverse=desc)
        if offset > 0:
            raw_messages = raw_messages[offset:]
        if limit > 0:
            raw_messages = raw_messages[:limit]

        unique_senders = {m[2] for m in raw_messages if m[2]}
        uncached = unique_senders - set(display_names)
        contact_names = self._batch_get_contact_names(uncached) if uncached else {}

        messages: list[Message] = []
        for seq, _talker, sender, msg_type, content, create_time, is_self in raw_messages:
            sender_name = (
                (display_names.get(sender) or contact_names.get(sender, sender))
                if sender
                else ""
            )
            messages.append(
                Message(
                    seq=seq,
                    time=datetime.fromtimestamp(create_time),
                    talker=_talker,
                    sender=sender,
                    sender_name=sender_name,
                    msg_type=msg_type,
                    content=content,
                    is_self=is_self,
                )
            )
        return messages

    def close(self) -> None:
        return
