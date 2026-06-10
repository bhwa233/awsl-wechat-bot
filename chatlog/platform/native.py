"""Chatlog 原生库 ctypes helper."""

from __future__ import annotations

import ctypes
import json
import os
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

_HEX_KEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_VENDOR_DIR = Path(__file__).resolve().parent.parent / "vendor"


class NativeQueryLibrary:
    def __init__(self, path_provider: Callable[[], Path]) -> None:
        self._path_provider = path_provider
        self._lib: ctypes.CDLL | None = None
        self._lock = threading.Lock()

    def query_raw_sql(self, db_path: str | Path, hex_key: str, sql: str) -> dict[str, Any]:
        if not _HEX_KEY_RE.match(hex_key):
            raise ValueError("hex_key 必须是 64 位十六进制字符串")

        lib = self._load_library()
        ptr = lib.AwslWechatChatlogQueryRawSQL(
            str(db_path).encode("utf-8"),
            hex_key.encode("ascii"),
            sql.encode("utf-8"),
        )
        if not ptr:
            raise RuntimeError("AwslWechatChatlogQueryRawSQL 返回 NULL")

        try:
            payload = ctypes.string_at(ptr).decode("utf-8")
        finally:
            lib.AwslWechatChatlogFreeString(ptr)

        result = json.loads(payload)
        if result.get("error"):
            raise RuntimeError(result["error"])
        return result

    def _load_library(self) -> ctypes.CDLL:
        if self._lib is not None:
            return self._lib
        with self._lock:
            if self._lib is not None:
                return self._lib
            path = self._path_provider()
            if not path.is_file():
                raise FileNotFoundError(f"chatlog v2 原生库不存在: {path}")
            try:
                dll = ctypes.CDLL(str(path))
            except OSError as exc:
                raise RuntimeError(f"加载 chatlog v2 原生库失败: {path}: {exc}") from exc
            dll.AwslWechatChatlogQueryRawSQL.argtypes = [
                ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p
            ]
            dll.AwslWechatChatlogQueryRawSQL.restype = ctypes.c_void_p
            dll.AwslWechatChatlogFreeString.argtypes = [ctypes.c_void_p]
            dll.AwslWechatChatlogFreeString.restype = None
            self._lib = dll
            return self._lib


def vendor_library_path(filename: str, *, fallback: str | None = None) -> Path:
    override = os.environ.get("AWSL_WECHAT_CHATLOG_LIBRARY")
    if override:
        return Path(override).expanduser()
    path = _VENDOR_DIR / filename
    if path.is_file() or not fallback:
        return path
    return _VENDOR_DIR / fallback
