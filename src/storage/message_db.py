"""消息去重数据库"""
import hashlib
import os
import sqlite3
import threading
import logging

logger = logging.getLogger(__name__)


class MessageDatabase:
    """消息哈希存储，支持群级别去重"""

    def __init__(self, db_path: str = None, max_cache: int = 200):
        if db_path is None:
            db_path = os.path.join(os.path.dirname(__file__), '..', '..', 'messages.db')
        self.db_path = os.path.abspath(db_path)
        self.max_cache = max_cache
        # 必须：SQLite 连接在多线程间共享需要锁保护
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._init_schema()

    def _init_schema(self):
        """初始化数据库表"""
        # 检查是否需要迁移旧表结构
        cursor = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='message_hashes'"
        )
        if cursor.fetchone():
            # 检查是否已经有 group_name 字段
            cursor = self._conn.execute("PRAGMA table_info(message_hashes)")
            columns = [row[1] for row in cursor.fetchall()]
            if 'group_name' not in columns:
                logger.info("检测到旧数据库结构，正在迁移...")
                self._conn.execute("DROP TABLE message_hashes")
                self._conn.commit()

        # 创建新表结构（包含 group_name）
        self._conn.execute('''
            CREATE TABLE IF NOT EXISTS message_hashes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_name TEXT NOT NULL,
                hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(group_name, hash)
            )
        ''')
        self._conn.commit()
        logger.debug("数据库初始化完成")

    def hash_message_with_context(self, messages: list, index: int, group_name: str) -> str:
        """结合前向上下文和群名计算消息的唯一哈希值"""
        current = messages[index]
        context_size = 2
        context_parts = []
        for i in range(max(0, index - context_size), index):
            context_parts.append(messages[i])
        context_parts.append(current)
        context = "|".join(context_parts)
        # 包含群名，避免不同群的相同消息被误判为重复
        return hashlib.sha256(f"{group_name}:{context}".encode()).hexdigest()[:16]

    def is_processed(self, msg_hash: str, group_name: str) -> bool:
        """检查消息是否已处理（群级别）"""
        with self._lock:
            cursor = self._conn.execute(
                'SELECT 1 FROM message_hashes WHERE hash = ? AND group_name = ?',
                (msg_hash, group_name)
            )
            return cursor.fetchone() is not None

    def mark_processed(self, msg_hash: str, group_name: str):
        """标记消息为已处理（群级别）"""
        with self._lock:
            try:
                self._conn.execute(
                    'INSERT OR IGNORE INTO message_hashes (hash, group_name) VALUES (?, ?)',
                    (msg_hash, group_name)
                )
                self._conn.commit()
            except sqlite3.Error as e:
                logger.error(f"数据库写入失败: {e}")

    def cleanup_old_hashes(self):
        """清理旧记录"""
        with self._lock:
            cursor = self._conn.execute('SELECT COUNT(*) FROM message_hashes')
            count = cursor.fetchone()[0]
            if count > self.max_cache:
                self._conn.execute('''
                    DELETE FROM message_hashes WHERE id IN (
                        SELECT id FROM message_hashes ORDER BY id ASC LIMIT ?
                    )
                ''', (count - self.max_cache // 2,))
                self._conn.commit()

    def close(self):
        """关闭数据库连接"""
        self._conn.close()
