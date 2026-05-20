"""消息去重数据库 — SQLModel ORM"""
import hashlib
import logging
import os

from sqlalchemy import Index
from sqlmodel import Field, Session, SQLModel, col, create_engine, delete, func, select

logger = logging.getLogger(__name__)


class MessageHash(SQLModel, table=True):
    __tablename__ = "message_hashes"
    __table_args__ = (
        Index("ix_group_hash", "group_name", "hash"),
        {"extend_existing": True},
    )

    id: int | None = Field(default=None, primary_key=True)
    group_name: str = Field(index=True)
    hash: str
    created_at: str | None = Field(default=None, sa_column_kwargs={"server_default": "CURRENT_TIMESTAMP"})


class MessageDatabase:
    """消息哈希存储,支持群级别去重"""

    def __init__(self, db_path: str | None = None, max_cache: int = 200):
        if db_path is None:
            db_path = os.path.join(os.path.dirname(__file__), '..', '..', 'messages.db')
        db_path = os.path.abspath(db_path)
        self.max_cache = max_cache
        self._engine = create_engine(f"sqlite:///{db_path}", echo=False)
        SQLModel.metadata.create_all(self._engine)
        logger.debug("数据库初始化完成")

    def hash_message_with_context(self, messages: list, index: int, group_name: str) -> str:
        """结合前向上下文和群名计算消息的唯一哈希值.

        注意: gateway.adapters.linux.LinuxWeChatAdapter._HASH_CONTEXT_SIZE 必须 >= 此处
        context_size, 否则 Linux 平台同一短文本会撞同一 hash 被永久拦截. 如需调整请同步.
        """
        current = messages[index]
        context_size = 2
        context_parts = []
        for i in range(max(0, index - context_size), index):
            context_parts.append(messages[i])
        context_parts.append(current)
        context = "|".join(context_parts)
        return hashlib.sha256(f"{group_name}:{context}".encode()).hexdigest()[:16]

    def is_processed(self, msg_hash: str, group_name: str) -> bool:
        """检查消息是否已处理(群级别)"""
        with Session(self._engine) as session:
            result = session.exec(
                select(MessageHash)
                .where(MessageHash.hash == msg_hash, MessageHash.group_name == group_name)
                .limit(1)
            ).first()
            return result is not None

    def mark_processed(self, msg_hash: str, group_name: str):
        """标记消息为已处理(群级别)"""
        with Session(self._engine) as session:
            existing = session.exec(
                select(MessageHash)
                .where(MessageHash.hash == msg_hash, MessageHash.group_name == group_name)
                .limit(1)
            ).first()
            if not existing:
                session.add(MessageHash(group_name=group_name, hash=msg_hash))
                session.commit()

    def cleanup_old_hashes(self, group_name: str | None = None):
        """清理旧记录(按群)— 批量删除"""
        with Session(self._engine) as session:
            base = select(func.count()).select_from(MessageHash)
            if group_name:
                base = base.where(MessageHash.group_name == group_name)
            count = session.exec(base).one()
            if count <= self.max_cache:
                return
            delete_count = count - self.max_cache // 2
            id_query = select(MessageHash.id).order_by(col(MessageHash.id).asc())
            if group_name:
                id_query = id_query.where(MessageHash.group_name == group_name)
            old_ids = session.exec(id_query.limit(delete_count)).all()
            if old_ids:
                session.exec(delete(MessageHash).where(col(MessageHash.id).in_(old_ids)))
                session.commit()

    def close(self):
        """关闭数据库连接"""
        self._engine.dispose()
