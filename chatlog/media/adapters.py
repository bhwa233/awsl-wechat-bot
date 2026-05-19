"""MediaResolver 的 v1/v2 数据库访问 adapter.

把"读 message_resource / VoiceInfo / kNonStoreEmoticonTable"统一成 SQL 接口,
屏蔽 v1 (pycryptodome 离线解密 → sqlite3) 和 v2 (原生库 raw SQL on 密文) 的差异.

跟 chatlog/platform/ 同构, 但维度不同:
- platform: OS 差异 (Linux/Windows 原生库路径)
- adapters: backend 差异 (v1 解密落盘 / v2 在线读密文)

SQL 接口约定: query(db_path, sql, params=()), sql 用 ? 占位符, params 是 tuple.
- v1 sqlite3 原生支持参数化.
- v2 原生库 C API 不支持参数, 在 adapter 内手动转义 (int 直拼, str 限 alphanum + 单引号转义).
"""

from __future__ import annotations

import glob as _glob
import re
import sqlite3
import threading
from abc import ABC, abstractmethod
from collections import OrderedDict
from pathlib import Path

_V2_STR_SAFE = re.compile(r"^[A-Za-z0-9_@.\-]{1,128}$")


class MediaAdapter(ABC):
    """v1/v2 数据库访问抽象, 只暴露 resolver 需要的 3 类表.

    media_dbs 是 list: WeChat 4.x 在消息量大时 voice 会分片 media_0/1/2.db,
    decode_voice 需要按顺序查直到命中.
    """

    def __init__(
        self,
        resource_db: Path | None,
        media_dbs: list[Path],
        emoticon_db: Path | None,
    ):
        self.resource_db = resource_db
        self.media_dbs = media_dbs
        self.emoticon_db = emoticon_db

    @abstractmethod
    def query(self, db_path: Path, sql: str, params: tuple = ()) -> list[list]:
        """跑 SQL, 返回 rows. sql 用 ? 占位, params 按出现顺序填入."""

    def has(self, db_path: Path | None) -> bool:
        return db_path is not None and db_path.is_file()

    def available_media_dbs(self) -> list[Path]:
        return [p for p in self.media_dbs if self.has(p)]


class V2NativeAdapter(MediaAdapter):
    """v2: 通过 chatlog.platform 原生库直接读密文 db (SQLCipher v4).

    原生库 C API 不接受参数列表, 在此手动转义并替换 ? 占位符.
    """

    def __init__(
        self,
        db_storage_dir: Path,
        wechat_db_key_json: str,
        resource_db: Path | None,
        media_dbs: list[Path],
        emoticon_db: Path | None,
    ):
        super().__init__(resource_db, media_dbs, emoticon_db)
        self.db_storage_dir = db_storage_dir
        self.wechat_db_key_json = wechat_db_key_json
        # platform.resolve_db_key 在 Linux 每次 json.loads, 缓存 db_path → key.
        # 单账号通常 3~5 个 db, 32 条上限兜底防止极端路径变化累积.
        # adapter 是 binding cached_property 跨请求复用 → 需要锁保护 OrderedDict.
        self._key_cache: OrderedDict[str, str] = OrderedDict()
        self._key_cache_max = 32
        self._key_cache_lock = threading.Lock()

    def query(self, db_path: Path, sql: str, params: tuple = ()) -> list[list]:
        from chatlog.platform import get_chatlog_platform

        if params:
            sql = _v2_inline_params(sql, params)
        plat = get_chatlog_platform()
        db_path_str = str(db_path)
        with self._key_cache_lock:
            key = self._key_cache.get(db_path_str)
            if key is not None:
                self._key_cache.move_to_end(db_path_str)
        if key is None:
            key = plat.resolve_db_key(
                str(self.db_storage_dir), db_path_str, self.wechat_db_key_json,
            )
            with self._key_cache_lock:
                self._key_cache[db_path_str] = key
                self._key_cache.move_to_end(db_path_str)
                while len(self._key_cache) > self._key_cache_max:
                    self._key_cache.popitem(last=False)
        result = plat.query_raw_sql(db_path_str, key, sql)
        return result.get("rows", []) or []


class V1SqliteAdapter(MediaAdapter):
    """v1: 离线解密后落盘到 WECHAT_DB_OUTPUT_PATH, sqlite3 直读明文."""

    def query(self, db_path: Path, sql: str, params: tuple = ()) -> list[list]:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            cur = conn.execute(sql, params)
            return [list(row) for row in cur.fetchall()]
        finally:
            conn.close()


def _v2_inline_params(sql: str, params: tuple) -> str:
    """V2 原生库 C API 没参数化, 手动转义 + 顺序替换 ?.

    只支持 int 和受限 str (alphanum + _@.- 1~128 字符), 其他类型直接拒绝.
    """
    parts = sql.split("?")
    if len(parts) - 1 != len(params):
        raise ValueError(f"SQL 占位符数 ({len(parts) - 1}) 与 params 长度 ({len(params)}) 不符")
    out = [parts[0]]
    for literal, tail in zip(_to_sql_literal_iter(params), parts[1:]):
        out.append(literal)
        out.append(tail)
    return "".join(out)


def _to_sql_literal_iter(params: tuple):
    for p in params:
        if isinstance(p, bool):
            yield "1" if p else "0"
        elif isinstance(p, int):
            yield str(p)
        elif isinstance(p, str):
            if not _V2_STR_SAFE.match(p):
                raise ValueError(f"V2 adapter 仅支持 alphanum/_@.- 字符串: {p!r}")
            yield f"'{p}'"
        else:
            raise TypeError(f"V2 adapter 不支持 param 类型 {type(p).__name__}: {p!r}")


def _discover_media_dbs(parent: Path) -> list[Path]:
    """按 media_\\d+.db 排序返回所有分片 (media_0.db, media_1.db, ...)."""
    pattern = str(parent / "media_*.db")
    hits = _glob.glob(pattern)
    media_re = re.compile(r"^media_(\d+)\.db$")
    indexed: list[tuple[int, Path]] = []
    for h in hits:
        name = Path(h).name
        m = media_re.match(name)
        if m:
            indexed.append((int(m.group(1)), Path(h)))
    indexed.sort(key=lambda x: x[0])
    return [p for _, p in indexed]


def build_v2_adapter(
    db_storage_dir: Path,
    wechat_db_key_json: str,
) -> V2NativeAdapter:
    msg_dir = db_storage_dir / "message"
    media_dbs = _discover_media_dbs(msg_dir) or [msg_dir / "media_0.db"]
    return V2NativeAdapter(
        db_storage_dir=db_storage_dir,
        wechat_db_key_json=wechat_db_key_json,
        resource_db=msg_dir / "message_resource.db",
        media_dbs=media_dbs,
        emoticon_db=db_storage_dir / "emoticon" / "emoticon.db",
    )


def build_v1_adapter(decrypted_dir: Path) -> V1SqliteAdapter:
    """v1 decryptor 把所有 db 铺平到 decrypted_dir 根 (跟 v1 reader 约定一致)."""
    media_dbs = _discover_media_dbs(decrypted_dir) or [decrypted_dir / "media_0.db"]
    return V1SqliteAdapter(
        resource_db=decrypted_dir / "message_resource.db",
        media_dbs=media_dbs,
        emoticon_db=decrypted_dir / "emoticon.db",
    )
