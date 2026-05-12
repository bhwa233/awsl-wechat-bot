"""Windows Chatlog 平台实现."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from chatlog.platform.base import ChatlogPlatform
from chatlog.platform.native import NativeQueryLibrary, vendor_library_path
from chatlog.platform.utils import is_db_storage_dir


class WindowsChatlogPlatform(ChatlogPlatform):
    def __init__(self) -> None:
        self._native = NativeQueryLibrary(self.library_path)

    def v1_config_error(self, config: Any) -> str | None:
        if not config.WECHAT_DB_INPUT_PATH:
            return "微信数据库输入目录未配置"
        if not config.WECHAT_DB_OUTPUT_PATH:
            return "微信数据库输出目录未配置"
        if not config.WECHAT_DB_KEY:
            return "微信数据库密钥未配置"
        return None

    def v2_config_error(self, config: Any) -> str | None:
        if not config.WECHAT_DB_INPUT_PATH:
            return "微信数据库输入目录未配置"
        if not config.WECHAT_DB_KEY:
            return "微信数据库密钥未配置"
        if not os.path.isdir(config.WECHAT_DB_INPUT_PATH):
            return f"加密目录不存在: {config.WECHAT_DB_INPUT_PATH}"
        native_path = self.library_path()
        if not native_path.is_file():
            return f"原生库不存在: {native_path}"
        return None

    def library_path(self) -> Path:
        return vendor_library_path("awsl_wechat_chatlog.dll")

    def resolve_db_storage_dir(self, input_dir: str) -> str:
        root = os.path.abspath(input_dir)
        candidates = [root, os.path.join(root, "db_storage")]
        for path in candidates:
            if is_db_storage_dir(path):
                return path
        return candidates[-1]

    def resolve_db_key(self, db_storage_dir: str, db_path: str, fallback_key: str) -> str:
        return fallback_key

    def uses_direct_db_key(self) -> bool:
        return False

    def query_raw_sql(self, db_path: str | Path, hex_key: str, sql: str) -> dict[str, Any]:
        return self._native.query_raw_sql(db_path, hex_key, sql)
