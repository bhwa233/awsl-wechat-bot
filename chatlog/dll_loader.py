"""awsl-wechat-chatlog DLL 加载与封装（v2 后端）"""

from __future__ import annotations

import ctypes
import json
import re
import sys
import threading
from pathlib import Path
from typing import Any

_DLL_PATH = Path(__file__).parent / "vendor" / "awsl_wechat_chatlog.dll"

_HEX_KEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")

_lib: ctypes.CDLL | None = None
_lock = threading.Lock()


def _load_library() -> ctypes.CDLL:
    global _lib
    if _lib is not None:
        return _lib
    with _lock:
        if _lib is not None:
            return _lib
        if sys.platform != "win32":
            raise RuntimeError(
                f"chatlog v2 后端依赖 awsl_wechat_chatlog.dll，仅支持 Windows，当前平台: {sys.platform}"
            )
        if not _DLL_PATH.is_file():
            raise FileNotFoundError(f"DLL 不存在: {_DLL_PATH}")
        dll = ctypes.CDLL(str(_DLL_PATH))
        dll.AwslWechatChatlogQueryRawSQL.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p
        ]
        dll.AwslWechatChatlogQueryRawSQL.restype = ctypes.c_void_p
        dll.AwslWechatChatlogFreeString.argtypes = [ctypes.c_void_p]
        dll.AwslWechatChatlogFreeString.restype = None
        dll.AwslWechatChatlogDecodeEmoticonMessage.argtypes = [
            ctypes.c_char_p, ctypes.c_char_p,
            ctypes.POINTER(ctypes.c_longlong), ctypes.POINTER(ctypes.c_char_p),
        ]
        dll.AwslWechatChatlogDecodeEmoticonMessage.restype = ctypes.c_void_p
        dll.AwslWechatChatlogFreeBytes.argtypes = [ctypes.c_void_p]
        dll.AwslWechatChatlogFreeBytes.restype = None
        _lib = dll
        return _lib


def query_raw_sql(db_path: str | Path, hex_key: str, sql: str) -> dict[str, Any]:
    """对 SQLCipher v4 微信数据库执行 raw SQL，返回 {columns, rows}。"""
    if not _HEX_KEY_RE.match(hex_key):
        raise ValueError("hex_key 必须是 64 位十六进制字符串")

    lib = _load_library()
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


def decode_emoticon(message_xml: str, wechat_data_dir: str) -> bytes:
    """解码表情包消息 XML 为图片 bytes。"""
    lib = _load_library()
    out_len = ctypes.c_longlong(0)
    out_error = ctypes.c_char_p(None)

    ptr = lib.AwslWechatChatlogDecodeEmoticonMessage(
        message_xml.encode("utf-8"),
        wechat_data_dir.encode("utf-8"),
        ctypes.byref(out_len),
        ctypes.byref(out_error),
    )

    if not ptr:
        err_msg = "decode_emoticon 返回 NULL"
        if out_error.value:
            err_msg = out_error.value.decode("utf-8")
            lib.AwslWechatChatlogFreeString(ctypes.cast(out_error, ctypes.c_void_p))
        raise RuntimeError(err_msg)

    try:
        return ctypes.string_at(ptr, out_len.value)
    finally:
        lib.AwslWechatChatlogFreeBytes(ptr)
