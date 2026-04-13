"""SQLite 版本化迁移工具。"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable


MigrationFn = Callable[[sqlite3.Connection], None]


class SQLiteMigrator:
    """轻量级 SQLite 迁移器。

    - 使用 `schema_migrations` 记录已执行版本
    - 同一版本只执行一次
    - 多次启动安全
    """

    def __init__(self, db_path: str):
        self.db_path = db_path

    def migrate(self, migrations: list[tuple[str, MigrationFn]]):
        with sqlite3.connect(self.db_path) as conn:
            self._ensure_schema_migrations(conn)
            for version, fn in migrations:
                if self._is_applied(conn, version):
                    continue
                conn.execute("BEGIN IMMEDIATE")
                try:
                    if self._is_applied(conn, version):
                        conn.commit()
                        continue
                    fn(conn)
                    conn.execute(
                        "INSERT INTO schema_migrations(version) VALUES (?)",
                        (version,),
                    )
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

    @staticmethod
    def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def column_names(conn: sqlite3.Connection, table_name: str) -> set[str]:
        if not SQLiteMigrator.table_exists(conn, table_name):
            return set()
        rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {row[1] for row in rows}

    @staticmethod
    def add_column_if_missing(conn: sqlite3.Connection, table_name: str, column_sql: str, column_name: str):
        if column_name in SQLiteMigrator.column_names(conn, table_name):
            return
        conn.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_sql}")

    @staticmethod
    def _ensure_schema_migrations(conn: sqlite3.Connection):
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

    @staticmethod
    def _is_applied(conn: sqlite3.Connection, version: str) -> bool:
        row = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            (version,),
        ).fetchone()
        return row is not None
