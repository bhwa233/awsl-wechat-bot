"""Linux Chatlog 平台实现。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from chatlog.platform.base import ChatlogPlatform
from chatlog.platform.native import NativeQueryLibrary, vendor_library_path
from chatlog.platform.utils import is_db_storage_dir

_HEX_KEY_RE = re.compile(r"^[0-9a-fA-F]{64}$")


class LinuxChatlogPlatform(ChatlogPlatform):
    def __init__(self) -> None:
        self._native = NativeQueryLibrary(self.library_path)

    def v1_config_error(self, config: Any) -> str | None:
        if not config.WECHAT_DB_INPUT_PATH:
            return "微信数据库输入目录未配置"
        if not config.WECHAT_DB_OUTPUT_PATH:
            return "微信数据库输出目录未配置"
        if not config.WECHAT_DB_KEY:
            return "Linux WECHAT_DB_KEY 未配置"
        if not self._load_linux_keys(config.WECHAT_DB_KEY):
            return "Linux WECHAT_DB_KEY 必须是 per-db key JSON"
        return None

    def v2_config_error(self, config: Any) -> str | None:
        if not config.WECHAT_DB_INPUT_PATH:
            return "微信数据库输入目录未配置"
        if not config.WECHAT_DB_KEY:
            return "Linux WECHAT_DB_KEY 未配置"
        if not os.path.isdir(config.WECHAT_DB_INPUT_PATH):
            return f"加密目录不存在: {config.WECHAT_DB_INPUT_PATH}"
        if not self._load_linux_keys(config.WECHAT_DB_KEY):
            return "Linux WECHAT_DB_KEY 必须是 per-db key JSON"
        native_path = self.library_path()
        if not native_path.is_file():
            return f"原生库不存在: {native_path}"
        return None

    def library_path(self) -> Path:
        return vendor_library_path("libawsl_wechat_chatlog.so")

    def resolve_db_storage_dir(self, input_dir: str) -> str:
        root = os.path.abspath(input_dir)
        candidates = [
            root,
            os.path.join(root, "db_storage"),
            *self._xwechat_user_db_dirs(root),
        ]
        for path in candidates:
            if is_db_storage_dir(path):
                return path
        return candidates[-1]

    def resolve_db_key(self, db_storage_dir: str, db_path: str, fallback_key: str) -> str:
        from common.config import config

        keys = self._load_linux_keys(config.WECHAT_DB_KEY)
        if not keys:
            raise ValueError("Linux WECHAT_DB_KEY 必须是 per-db key JSON")
        rel = os.path.relpath(db_path, db_storage_dir).replace(os.sep, "/")
        key = keys.get(rel)
        if key and _HEX_KEY_RE.match(key):
            return key
        if key:
            raise ValueError(f"Linux per-db key 必须是 64 位十六进制字符串: {rel}")
        raise ValueError(f"Linux per-db key 不存在: {rel}")

    def uses_direct_db_key(self) -> bool:
        return True

    def query_raw_sql(self, db_path: str | Path, hex_key: str, sql: str) -> dict[str, Any]:
        return self._native.query_raw_sql(db_path, hex_key, sql)

    @staticmethod
    def _load_linux_keys(value: str) -> dict[str, str]:
        value = value.strip()
        if not value:
            return {}
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if not isinstance(data, dict):
            return {}

        keys: dict[str, str] = {}
        for path, key_data in data.items():
            key = LinuxChatlogPlatform._extract_key_value(key_data)
            if not key:
                continue
            keys[str(path)] = key
        return keys

    @staticmethod
    def _extract_key_value(value: Any) -> str | None:
        if isinstance(value, str):
            return value
        if isinstance(value, dict):
            enc_key = value.get("enc_key")
            if isinstance(enc_key, str):
                return enc_key
        return None

    @staticmethod
    def _xwechat_user_db_dirs(root: str) -> list[str]:
        if not os.path.isdir(root):
            return []

        result: list[str] = []
        for name in os.listdir(root):
            storage = os.path.join(root, name, "db_storage")
            if os.path.isdir(storage):
                result.append(storage)

        result.sort(key=lambda path: os.path.getmtime(path), reverse=True)
        return result
