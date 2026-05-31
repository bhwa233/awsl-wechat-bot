"""Chatlog 平台特性接口."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class ChatlogPlatform(ABC):
    """不同系统的 chatlog 数据库路径、key 与原生库策略接口."""

    @abstractmethod
    def v1_config_error(self, config: Any) -> str | None:
        """返回 v1 配置错误信息.配置有效时返回 None."""

    @abstractmethod
    def v2_config_error(self, config: Any) -> str | None:
        """返回 v2 配置错误信息.配置有效时返回 None."""

    @abstractmethod
    def library_path(self) -> Path:
        """当前平台的原生库路径."""

    @abstractmethod
    def resolve_db_storage_dir(self, input_dir: str) -> str:
        """解析微信 db_storage 目录."""

    @abstractmethod
    def resolve_db_key(self, db_storage_dir: str, db_path: str, fallback_key: str) -> str:
        """解析当前 DB 文件应使用的 key."""

    @abstractmethod
    def uses_direct_db_key(self) -> bool:
        """当前平台是否直接使用 DB enc_key, 而不是从 raw key 派生."""

    @abstractmethod
    def query_raw_sql(self, db_path: str | Path, hex_key: str, sql: str) -> dict[str, Any]:
        """对 SQLCipher v4 微信数据库执行 raw SQL."""

    @abstractmethod
    def wechat_running(self) -> bool:
        """微信进程是否在运行."""
