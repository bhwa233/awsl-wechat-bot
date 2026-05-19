"""媒体缓存层 (进程级共享, 跨请求复用).

3 套缓存:
- sticker:   sticker md5 → (bytes, fmt), 字节预算 50MB
             避免重复 CDN 拉取 (~ms 加速)
- hd_image:  image md5 → (bytes, fmt), 字节预算 50MB
             含 wxgf 转码后 jpg + 原生 jpg/png 直出, HEVC 解码 ~500ms-1s
- emoticon_lookup: emoticon.db 路径 → md5 映射, 单账号场景一份, LRU 上限 32 兜底
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections import OrderedDict
from threading import Lock
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

K = TypeVar("K")

_MAX_BYTES = 50 * 1024 * 1024  # 50MB per cache type


class _ByteLRU(Generic[K]):
    """字节预算 LRU, value 形如 (bytes, fmt). 命中重排到末尾, 超预算淘汰头部."""

    def __init__(self, max_bytes: int) -> None:
        self._max_bytes = max_bytes
        self._data: OrderedDict[K, tuple[bytes, str]] = OrderedDict()
        self._size = 0
        self._lock = Lock()

    def get(self, key: K) -> tuple[bytes, str] | None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
                return self._data[key]
        return None

    def put(self, key: K, value: tuple[bytes, str]) -> None:
        blob, _ = value
        size = len(blob)
        # 单条超预算直接跳过, 避免反复淘汰
        if size > self._max_bytes:
            return
        with self._lock:
            if key in self._data:
                self._size -= len(self._data[key][0])
            self._data[key] = value
            self._data.move_to_end(key)
            self._size += size
            while self._size > self._max_bytes and self._data:
                _, (old_blob, _) = self._data.popitem(last=False)
                self._size -= len(old_blob)


# sticker md5 → (bytes, fmt), GIF 常 1-5MB.
sticker: _ByteLRU[str] = _ByteLRU(max_bytes=_MAX_BYTES)

# image md5 → (bytes, fmt), 高清图常 100KB~1MB; wxgf 转码慢 (~500ms-1s) 缓存收益高.
hd_image: _ByteLRU[str] = _ByteLRU(max_bytes=_MAX_BYTES)


# ---------- single-flight (hd_image HEVC 解码去重) ---------- #
# 同 md5 高清图被并发点开时, 多请求会同时跑 ~500ms-1s 的 PyAV HEVC 解码;
# 用 per-md5 Event 让先到的干活, 后到的 wait, 完成后统一从 cache 读结果.


class _InflightEvent:
    __slots__ = ("event",)

    def __init__(self) -> None:
        self.event = threading.Event()


_hd_inflight: dict[str, _InflightEvent] = {}
_hd_inflight_lock = Lock()


def hd_image_singleflight_acquire(md5: str) -> tuple[bool, _InflightEvent]:
    """返回 (是否由本调用方负责生产, 等待的 event 或 None).

    is_producer=True: 调用方需要解码后调用 hd_image_singleflight_release(md5).
    is_producer=False: 调用方应 event.wait() 然后再次 cache.hd_image.get(md5).
    """
    with _hd_inflight_lock:
        existing = _hd_inflight.get(md5)
        if existing is not None:
            return False, existing
        ev = _InflightEvent()
        _hd_inflight[md5] = ev
        return True, ev


def hd_image_singleflight_release(md5: str) -> None:
    with _hd_inflight_lock:
        ev = _hd_inflight.pop(md5, None)
    if ev is not None:
        ev.event.set()


# ---------- negative cache (hd_image 失败短期记忆) ---------- #
# hd_path 不存在 / 解密失败时, 短期内 (30s) 不重复 IO glob/解密, 避免 lightbox 反复点击造成
# 重复 IO. TTL 短到不影响 .dat 文件刚生成完的场景.

_HD_NEGATIVE_TTL = 30.0
_HD_NEGATIVE_MAX = 256
_hd_negative_cache: OrderedDict[str, float] = OrderedDict()
_hd_negative_lock = Lock()


def hd_image_negative_get(md5: str) -> bool:
    """True 表示当前 md5 在 negative cache 内, 跳过解码尝试."""
    now = time.monotonic()
    with _hd_negative_lock:
        exp = _hd_negative_cache.get(md5)
        if exp is None:
            return False
        if exp < now:
            del _hd_negative_cache[md5]
            return False
        _hd_negative_cache.move_to_end(md5)
        return True


def hd_image_negative_put(md5: str) -> None:
    exp = time.monotonic() + _HD_NEGATIVE_TTL
    with _hd_negative_lock:
        _hd_negative_cache[md5] = exp
        _hd_negative_cache.move_to_end(md5)
        while len(_hd_negative_cache) > _HD_NEGATIVE_MAX:
            _hd_negative_cache.popitem(last=False)


# ---------- emoticon lookup ---------- #
# emoticon.db.kNonStoreEmoticonTable 通常几千行, 每请求全表 SELECT 太重.
# 单账号一份, LRU 上限 32 兜底防止极端路径变化累积.
# 缓存 entry 含 mtime, 文件 mtime 变化 (例如 v1 /decrypt 后重写 emoticon.db) 自动失效.

_EMOTICON_LOOKUP_MAX = 32
# value: (mtime, lookup_dict)
_emoticon_lookup_cache: OrderedDict[str, tuple[float, dict[str, dict[str, str]]]] = OrderedDict()
_emoticon_lookup_lock = Lock()


def emoticon_lookup_for(adapter) -> dict[str, dict[str, str]]:
    """读 emoticon.db kNonStoreEmoticonTable 构建 md5 → (cdn_url, aes_key, encrypt_url)."""
    if not adapter.has(adapter.emoticon_db):
        return {}
    key = str(adapter.emoticon_db)
    try:
        cur_mtime = os.path.getmtime(adapter.emoticon_db)
    except OSError:
        cur_mtime = 0.0
    with _emoticon_lookup_lock:
        cached = _emoticon_lookup_cache.get(key)
        if cached is not None and cached[0] == cur_mtime:
            _emoticon_lookup_cache.move_to_end(key)
            return cached[1]
    try:
        rows = adapter.query(
            adapter.emoticon_db,
            "SELECT md5, aes_key, cdn_url, encrypt_url FROM kNonStoreEmoticonTable",
        )
    except Exception as exc:
        logger.warning("[sticker] emoticon.db 查询失败: %s", exc)
        return {}
    lookup: dict[str, dict[str, str]] = {}
    for row in rows:
        md5 = (row[0] or "").strip()
        if md5:
            lookup[md5] = {
                "aes_key": row[1] or "",
                "cdn_url": row[2] or "",
                "encrypt_url": row[3] or "",
            }
    logger.info("[sticker] emoticon.db 加载 %d 条映射 (mtime=%.0f)", len(lookup), cur_mtime)
    with _emoticon_lookup_lock:
        _emoticon_lookup_cache[key] = (cur_mtime, lookup)
        _emoticon_lookup_cache.move_to_end(key)
        while len(_emoticon_lookup_cache) > _EMOTICON_LOOKUP_MAX:
            _emoticon_lookup_cache.popitem(last=False)
    return lookup
