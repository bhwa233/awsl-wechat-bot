"""微信数据库读取器 v2 - 通过 awsl-wechat-chatlog 原生库在线查询 SQLCipher v4

与 v1 的差别:
- 不离线解密落盘,原生库直接打开密文 db
- 输入路径是 WECHAT_DB_INPUT_PATH,可为微信用户目录或 db_storage 目录
- 复用 v1 的 ChatRoom/Message dataclass、_parse_room_data、zstd 解压逻辑
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import sys
import threading
import time
from datetime import datetime
from typing import Any

from chatlog.platform import get_chatlog_platform
from chatlog.search import escape_like_query, message_search_expressions
from chatlog.wechat_db import (
    HAS_ZSTD,
    MESSAGE_TYPE_TEXT,
    ChatRoom,
    Contact,
    Message,
    RoomMember,
    _build_room_member,
    _parse_at_users,
    _parse_room_data,
    _parse_room_members,
)

if HAS_ZSTD:
    import zstandard as zstd

logger = logging.getLogger(__name__)

_TABLE_NAME_RE = re.compile(r"^Msg_[0-9a-f]{32}$")
_MESSAGE_DB_RE = re.compile(r"^message_([0-9]?[0-9])?\.db$")
_USERNAME_RE = re.compile(r"^[A-Za-z0-9_@.\-]{1,128}$")

# sort_seq ≈ create_time*1000 + 同秒内序号, 实测偏移落在 [0, ~4000ms];
# 用 sort_seq 索引替代 create_time 全表扫描时, 范围两端各放宽该余量再叠加精确过滤.
_SORT_SEQ_SLACK_MS = 10_000

# ---- 进程级缓存: reader 每请求新建, 这些状态跨请求复用以消除重复的原生库查询 ----
_CACHE_LOCK = threading.Lock()
# storage_dir -> (message db 文件集合, [(path, start_ts, end_ts)]); 文件集合变化时自动重建
_DB_TIMES_CACHE: dict[str, tuple[frozenset[str], list[tuple[str, int, int]]]] = {}
# (db_path, table) -> (过期时间, 是否存在); 存在缓存 1h, 不存在缓存 30s (新群建表后能较快被发现)
_TABLE_EXISTS_CACHE: dict[tuple[str, str], tuple[float, bool]] = {}
_TABLE_EXISTS_POS_TTL = 3600.0
_TABLE_EXISTS_NEG_TTL = 30.0
# (storage_dir, chatroom) -> (过期时间, 群内昵称表)
_DISPLAY_NAMES_CACHE: dict[tuple[str, str], tuple[float, dict[str, str]]] = {}
_DISPLAY_NAMES_TTL = 60.0
# (storage_dir, username) -> (过期时间, 显示名)
_CONTACT_NAMES_CACHE: dict[tuple[str, str], tuple[float, str]] = {}
_CONTACT_NAMES_TTL = 300.0

# 微信写库与读取并发时偶发的瞬时错误, 重试一次即可恢复
_TRANSIENT_DB_ERRORS = ("malformed", "not a database", "disk i/o", "database is locked")


def _validate_username(name: str) -> str:
    if not _USERNAME_RE.match(name):
        raise ValueError(f"非法的 username/talker: {name!r}")
    return name


def _talker_to_table_name(talker: str) -> str:
    return f"Msg_{hashlib.md5(talker.encode()).hexdigest()}"


def _quote_sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _decode_blob(value: Any) -> bytes | Any:
    """Go encoding/json 把 []byte 编码为 base64 字符串,对已知 BLOB 列调用此函数还原.
    只有解出来不是合法 UTF-8 文本时才认为是真正的 BLOB,避免误把纯文本 base64 化."""
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
    """v2 reader:直接读加密目录,原生库跑 raw SQL"""

    def __init__(self, encrypted_dir: str, hex_key: str):
        self.platform = get_chatlog_platform()
        self.db_storage_dir = self.platform.resolve_db_storage_dir(encrypted_dir)
        self.hex_key = hex_key
        self._message_db_times: list[tuple[str, int, int]] = []
        self._chatroom_display_names: dict[str, dict[str, str]] = {}

    def _contact_db_path(self) -> str:
        path = os.path.join(self.db_storage_dir, "contact", "contact.db")
        if not os.path.isfile(path):
            raise FileNotFoundError(f"联系人数据库不存在: {path}")
        return path

    def _list_message_db_paths(self) -> list[str]:
        message_dir = os.path.join(self.db_storage_dir, "message")
        if not os.path.isdir(message_dir):
            raise FileNotFoundError(f"消息数据库目录不存在: {message_dir}")
        files = [
            os.path.join(message_dir, f)
            for f in os.listdir(message_dir)
            if _MESSAGE_DB_RE.match(f)
        ]
        return sorted(files)

    def _query(self, db_path: str, sql: str) -> tuple[list[str], list[list]]:
        key = self.platform.resolve_db_key(self.db_storage_dir, db_path, self.hex_key)
        try:
            result = self.platform.query_raw_sql(db_path, key, sql)
        except RuntimeError as e:
            msg = str(e).lower()
            if not any(t in msg for t in _TRANSIENT_DB_ERRORS):
                raise
            time.sleep(0.05)
            result = self.platform.query_raw_sql(db_path, key, sql)
        return result.get("columns", []) or [], result.get("rows", []) or []

    def _init_message_dbs(self, strict: bool = False) -> None:
        if self._message_db_times and not strict:
            return
        paths = self._list_message_db_paths()
        file_set = frozenset(paths)
        with _CACHE_LOCK:
            cached = _DB_TIMES_CACHE.get(self.db_storage_dir)
        if cached and cached[0] == file_set:
            self._message_db_times = list(cached[1])
            return
        infos = []
        errors = []
        for path in paths:
            try:
                _, rows = self._query(path, "SELECT timestamp FROM Timestamp LIMIT 1")
                if rows and rows[0]:
                    infos.append((path, int(rows[0][0])))
                else:
                    raise RuntimeError("消息数据库缺少 Timestamp")
            except Exception as e:
                if strict:
                    raise
                errors.append((path, e))
                logger.warning("[ChatlogV2] 无法读取数据库 %s: %s", path, e)
        if not infos:
            detail = "; ".join(f"{p}: {e}" for p, e in errors) if errors else "目录下无 message db"
            raise RuntimeError(f"没有可用的消息数据库: {detail}")
        infos.sort(key=lambda x: x[1])
        self._message_db_times = []
        for i, (path, start_time) in enumerate(infos):
            end_time = infos[i + 1][1] if i < len(infos) - 1 else sys.maxsize
            self._message_db_times.append((path, start_time, end_time))
        # 仅在全部 db 都成功读出时间索引时缓存, 避免固化半残状态
        if not errors:
            with _CACHE_LOCK:
                _DB_TIMES_CACHE[self.db_storage_dir] = (file_set, list(self._message_db_times))

    def _get_dbs_for_time_range(self, start_ts: int, end_ts: int, strict: bool = False) -> list[str]:
        self._init_message_dbs(strict=strict)
        return [p for p, s, e in self._message_db_times if s <= end_ts and e >= start_ts]

    def _tables_exist(self, db_path: str, tables: list[str]) -> set[str]:
        """批量检查表是否存在(带进程级正/负缓存), 返回存在的表名集合."""
        now = time.time()
        exist: set[str] = set()
        misses: list[str] = []
        with _CACHE_LOCK:
            for t in tables:
                hit = _TABLE_EXISTS_CACHE.get((db_path, t))
                if hit and hit[0] > now:
                    if hit[1]:
                        exist.add(t)
                else:
                    misses.append(t)
        if misses:
            in_clause = ",".join(f"'{t}'" for t in misses)
            _, rows = self._query(
                db_path,
                f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({in_clause})",
            )
            found = {row[0] for row in rows}
            exist |= found
            with _CACHE_LOCK:
                for t in misses:
                    ok = t in found
                    ttl = _TABLE_EXISTS_POS_TTL if ok else _TABLE_EXISTS_NEG_TTL
                    _TABLE_EXISTS_CACHE[(db_path, t)] = (now + ttl, ok)
        return exist

    def list_groups(self, limit: int = 0) -> list[ChatRoom]:
        contact_db = self._contact_db_path()
        sql = (
            "SELECT c.username, COALESCE(cr.owner, ''), c.nick_name, c.remark, "
            "c.small_head_url, c.big_head_url "
            "FROM contact c "
            "LEFT JOIN chat_room cr ON c.username = cr.username "
            "WHERE ((c.local_type = 1 AND (c.flag & 2) != 0) "
            "OR (c.local_type = 2 AND c.is_in_chat_room = 1) "
            "OR c.local_type = 5) "
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
                avatar=row[4] or "",
                big_avatar=row[5] or "",
            )
            for row in rows
        ]

    def get_contacts(self, usernames: list[str]) -> list[Contact]:
        """批量查询联系人/群成员信息(含头像 URL)"""
        safe = []
        for u in usernames:
            try:
                _validate_username(u)
                safe.append(u)
            except ValueError:
                continue
        if not safe:
            return []
        in_clause = ",".join(f"'{u}'" for u in safe)
        contact_db = self._contact_db_path()
        _, rows = self._query(
            contact_db,
            f"SELECT username, nick_name, remark, alias, small_head_url, big_head_url "
            f"FROM contact WHERE username IN ({in_clause})",
        )
        return [
            Contact(
                username=row[0] or "",
                nick_name=row[1] or "",
                remark=row[2] or "",
                alias=row[3] or "",
                avatar=row[4] or "",
                big_avatar=row[5] or "",
            )
            for row in rows
        ]

    def get_room_members(self, chatroom: str) -> list[RoomMember]:
        """列出群成员: 群内昵称 + 联系人昵称/头像, 群主排首位"""
        _validate_username(chatroom)
        contact_db = self._contact_db_path()
        _, rows = self._query(
            contact_db,
            f"SELECT owner, ext_buffer FROM chat_room WHERE username='{chatroom}' LIMIT 1",
        )
        if not rows or not rows[0][1]:
            return []
        owner = rows[0][0] or ""
        ext_buffer = _decode_blob(rows[0][1])
        if isinstance(ext_buffer, str):
            ext_buffer = ext_buffer.encode("latin-1", errors="ignore")
        members = _parse_room_members(ext_buffer)
        if not members:
            return []

        display_map = {wxid: name for wxid, name in members}
        contacts = {c.username: c for c in self.get_contacts(list(display_map.keys()))}
        result = [
            _build_room_member(wxid, name, contacts.get(wxid), wxid == owner)
            for wxid, name in display_map.items()
        ]
        result.sort(key=lambda m: not m.is_owner)
        return result

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
        now = time.time()
        result: dict[str, str] = {}
        misses: list[str] = []
        with _CACHE_LOCK:
            for u in usernames:
                hit = _CONTACT_NAMES_CACHE.get((self.db_storage_dir, u))
                if hit and hit[0] > now:
                    result[u] = hit[1]
                else:
                    misses.append(u)
        safe = []
        for u in misses:
            try:
                _validate_username(u)
                safe.append(u)
            except ValueError:
                continue
        if not safe:
            return result
        in_clause = ",".join(f"'{u}'" for u in safe)
        contact_db = self._contact_db_path()
        _, rows = self._query(
            contact_db,
            f"SELECT username, nick_name, remark, alias FROM contact WHERE username IN ({in_clause})",
        )
        fetched: dict[str, str] = {}
        for row in rows:
            username = row[0] or ""
            name = row[2] or row[1] or row[3] or username
            fetched[username] = name
        with _CACHE_LOCK:
            for u in safe:
                # 查不到的也缓存为 wxid 本身, 避免每次重查
                _CONTACT_NAMES_CACHE[(self.db_storage_dir, u)] = (
                    now + _CONTACT_NAMES_TTL,
                    fetched.get(u, u),
                )
        for u in safe:
            result[u] = fetched.get(u, u)
        return result

    def _get_chatroom_display_names(self, chatroom: str) -> dict[str, str]:
        if chatroom in self._chatroom_display_names:
            return self._chatroom_display_names[chatroom]
        now = time.time()
        cache_key = (self.db_storage_dir, chatroom)
        with _CACHE_LOCK:
            hit = _DISPLAY_NAMES_CACHE.get(cache_key)
        if hit and hit[0] > now:
            self._chatroom_display_names[chatroom] = hit[1]
            return hit[1]
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
        with _CACHE_LOCK:
            _DISPLAY_NAMES_CACHE[cache_key] = (now + _DISPLAY_NAMES_TTL, names)
        return names

    def get_messages(
        self,
        talker: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        text_only: bool = True,
        limit: int = 0,
        offset: int = 0,
        desc: bool = False,
    ) -> list[Message]:
        return self._get_messages(
            talker=talker,
            start_time=start_time,
            end_time=end_time,
            text_only=text_only,
            limit=limit,
            offset=offset,
            desc=desc,
        )

    def search_messages(
        self,
        talker: str,
        *,
        query: str,
        senders: list[str],
        msg_types: list[int] | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
        desc: bool = False,
    ) -> list[Message]:
        return self._get_messages(
            talker=talker,
            start_time=start_time,
            end_time=end_time,
            text_only=False,
            msg_types=[1] if msg_types is None else msg_types,
            limit=limit,
            offset=offset,
            desc=desc,
            senders=senders,
            content_query=query,
        )

    def count_messages(
        self,
        talker: str,
        *,
        query: str,
        senders: list[str],
        msg_types: list[int] | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict[str, int]:
        return self._get_messages(
            talker=talker,
            start_time=start_time,
            end_time=end_time,
            text_only=False,
            msg_types=[1] if msg_types is None else msg_types,
            count_only=True,
            senders=senders,
            content_query=query,
        )

    def _get_messages(
        self,
        talker: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        text_only: bool = True,
        limit: int = 0,
        offset: int = 0,
        desc: bool = False,
        senders: list[str] | None = None,
        content_query: str = "",
        msg_types: list[int] | None = None,
        count_only: bool = False,
    ) -> list[Message] | dict[str, int]:
        _validate_username(talker)
        start_time = start_time or datetime(2000, 1, 1)
        end_time = end_time or datetime.now()
        start_ts, end_ts = int(start_time.timestamp()), int(end_time.timestamp())

        db_paths = self._get_dbs_for_time_range(start_ts, end_ts, strict=count_only)
        if not db_paths:
            return {} if count_only else []

        table_name = _talker_to_table_name(talker)
        if not _TABLE_NAME_RE.match(table_name):
            raise ValueError(f"Invalid table name derived from talker: {talker}")
        is_chatroom = talker.endswith("@chatroom")
        display_names = self._get_chatroom_display_names(talker) if is_chatroom and not count_only else {}
        by_sender: dict[str, int] = {}
        search_sender, search_content = message_search_expressions(is_chatroom)

        type_filter = f" AND (m.local_type & 0xFFFFFFFF) = {MESSAGE_TYPE_TEXT}" if text_only else ""
        if msg_types:
            type_values = ",".join(str(int(value)) for value in msg_types)
            type_filter += f" AND (m.local_type & 0xFFFFFFFF) IN ({type_values})"
        if content_query:
            type_filter += f" AND (m.local_type & 0xFFFFFFFF) = {MESSAGE_TYPE_TEXT}"
        sender_filter = ""
        if senders:
            for sender in senders:
                _validate_username(sender)
            sender_values = ",".join(_quote_sql_literal(sender) for sender in senders)
            sender_filter = f" AND {search_sender} IN ({sender_values})"
        content_filter = ""
        if content_query:
            pattern = _quote_sql_literal(f"%{escape_like_query(content_query)}%")
            content_filter = (
                " AND hex(substr(m.message_content, 1, 4)) != '28B52FFD'"
                f" AND {search_content} LIKE {pattern} ESCAPE '\\' COLLATE NOCASE"
            )
        sql_limit = ""
        if limit > 0:
            sql_limit = f" LIMIT {int(offset + limit)}"
        # create_time 无索引; 用 sort_seq(有索引, ≈create_time*1000) 圈定范围避免全表扫描,
        # 两端放宽余量后仍叠加精确的 create_time 过滤, 结果不变
        seq_lo = start_ts * 1000 - _SORT_SEQ_SLACK_MS
        seq_hi = end_ts * 1000 + _SORT_SEQ_SLACK_MS

        raw_messages: list[tuple] = []
        for db_path in db_paths:
            try:
                if table_name not in self._tables_exist(db_path, [table_name]):
                    continue
                columns = f"{search_sender}, COUNT(*)" if count_only else (
                    "m.sort_seq, m.server_id, m.local_type, n.user_name, m.local_id, "
                    "m.create_time, m.message_content, m.status, m.source"
                )
                sql = (
                    f"SELECT {columns} "
                    f"FROM {table_name} m "
                    f"LEFT JOIN Name2Id n ON m.real_sender_id = n.rowid "
                    f"WHERE m.sort_seq >= {seq_lo} AND m.sort_seq <= {seq_hi} "
                    f"AND m.create_time >= {start_ts} AND m.create_time <= {end_ts}"
                    f"{type_filter}{sender_filter}{content_filter} "
                )
                if count_only:
                    sql += f"GROUP BY {search_sender}"
                if not count_only:
                    sql += f"ORDER BY m.sort_seq {'DESC' if desc else 'ASC'}{sql_limit}"
                _, rows = self._query(db_path, sql)
            except Exception as e:
                if count_only:
                    raise
                logger.warning("[ChatlogV2] 查询 %s 失败: %s", db_path, e)
                continue
            if count_only:
                for sender, count in rows:
                    by_sender[sender] = by_sender.get(sender, 0) + int(count)
                continue
            raw_messages.extend(self._process_rows(talker, is_chatroom, rows))

        if count_only:
            return by_sender

        raw_messages.sort(key=lambda m: m[0], reverse=desc)
        if offset > 0:
            raw_messages = raw_messages[offset:]
        if limit > 0:
            raw_messages = raw_messages[:limit]

        return self._finalize_messages(raw_messages, display_names)

    def _process_rows(self, talker: str, is_chatroom: bool, rows: list[list]) -> list[tuple]:
        """把原生库查询行转成 raw_message 元组(与列顺序耦合, 供 get_messages/get_messages_since 复用)."""
        raw_messages: list[tuple] = []
        for row in rows:
            sort_seq, server_id, local_type, user_name, local_id, create_time, message_content, status, source = row
            msg_type = int(local_type) & 0xFFFFFFFF

            content = _decompress_content(_decode_blob(message_content))
            at_users = _parse_at_users(source) if is_chatroom else []
            sender = user_name or ""
            if is_chatroom and ":\n" in content:
                parts = content.split(":\n", 1)
                if len(parts) == 2 and " " not in parts[0] and "\n" not in parts[0]:
                    sender, content = parts

            raw_messages.append((
                int(sort_seq), talker, sender, msg_type, content,
                int(create_time),
                # voip (local_type=50) status 恒为 2 不可靠, 退化纯 sender 判
                (not is_chatroom and talker != sender) if msg_type == 50
                else (status == 2 or (not is_chatroom and talker != sender)),
                int(server_id) if server_id is not None else 0,
                int(local_id) if local_id is not None else 0,
                at_users,
            ))
        return raw_messages

    def _finalize_messages(
        self,
        raw_messages: list[tuple],
        display_names: dict[str, str],
        contact_names: dict[str, str] | None = None,
    ) -> list[Message]:
        """补全 sender_name 并构造 Message; contact_names 缺省时按需批量查询."""
        if contact_names is None:
            unique_senders = {m[2] for m in raw_messages if m[2]}
            uncached = unique_senders - set(display_names)
            contact_names = self._batch_get_contact_names(uncached) if uncached else {}

        messages: list[Message] = []
        for seq, _talker, sender, msg_type, content, create_time, is_self, server_id, local_id, at_users in raw_messages:
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
                    server_id=server_id,
                    local_id=local_id,
                    at_users=at_users,
                )
            )
        return messages

    def get_messages_since(
        self,
        items: list[tuple[str, int]],
        limit: int = 500,
    ) -> dict[str, list[Message]]:
        """批量增量查询: 每个 (talker, since_seq) 返回 sort_seq >= since_seq 的消息(升序, 每群最多 limit 条).

        专为低延迟轮询设计: 全部群合并为对每个 message db 的一条 UNION ALL SQL,
        sort_seq 走索引, 整批耗时与单群查询同量级. since_seq 为闭区间(>=),
        调用方用上次返回的最大 seq 做游标并自行去重边界行.
        """
        seen: dict[str, int] = {}
        for talker, since_seq in items:
            _validate_username(talker)
            since = int(since_seq)
            # 同一群取最小 since, 去重
            if talker not in seen or since < seen[talker]:
                seen[talker] = since
        if not seen:
            return {}
        limit = max(1, int(limit))

        # since_seq ≈ 毫秒时间戳, 换算成秒并放宽余量, 只查可能覆盖该范围的 message db
        min_start_ts = min(seen.values()) // 1000 - _SORT_SEQ_SLACK_MS // 1000
        db_paths = self._get_dbs_for_time_range(min_start_ts, sys.maxsize)

        tables = {talker: _talker_to_table_name(talker) for talker in seen}
        results_raw: dict[str, list[tuple]] = {talker: [] for talker in seen}
        cols = (
            "m.sort_seq, m.server_id, m.local_type, n.user_name, "
            "m.local_id, m.create_time, m.message_content, m.status, m.source"
        )
        for db_path in db_paths:
            exist = self._tables_exist(db_path, list(tables.values()))
            parts = []
            ordered_talkers = []
            for talker, table in tables.items():
                if table not in exist:
                    continue
                ordered_talkers.append(talker)
                parts.append(
                    f"SELECT * FROM (SELECT '{talker}' AS talker, {cols} "
                    f"FROM {table} m LEFT JOIN Name2Id n ON m.real_sender_id = n.rowid "
                    f"WHERE m.sort_seq >= {seen[talker]} "
                    f"ORDER BY m.sort_seq ASC LIMIT {limit})"
                )
            if not parts:
                continue
            try:
                _, rows = self._query(db_path, " UNION ALL ".join(parts))
            except Exception as e:
                logger.warning("[ChatlogV2] 批量查询 %s 失败: %s", db_path, e)
                continue
            by_talker: dict[str, list[list]] = {}
            for row in rows:
                by_talker.setdefault(row[0], []).append(row[1:])
            for talker, talker_rows in by_talker.items():
                is_chatroom = talker.endswith("@chatroom")
                results_raw[talker].extend(self._process_rows(talker, is_chatroom, talker_rows))

        # 跨群一次性解析 sender 显示名, 避免按群重复查 contact 库
        all_senders: set[str] = set()
        display_by_talker: dict[str, dict[str, str]] = {}
        for talker, raws in results_raw.items():
            if not raws:
                continue
            display = (
                self._get_chatroom_display_names(talker)
                if talker.endswith("@chatroom")
                else {}
            )
            display_by_talker[talker] = display
            all_senders |= {m[2] for m in raws if m[2] and m[2] not in display}
        contact_names = self._batch_get_contact_names(all_senders) if all_senders else {}

        results: dict[str, list[Message]] = {}
        for talker, raws in results_raw.items():
            raws.sort(key=lambda m: m[0])
            results[talker] = self._finalize_messages(
                raws[:limit], display_by_talker.get(talker, {}), contact_names
            )
        return results

    def close(self) -> None:
        return
