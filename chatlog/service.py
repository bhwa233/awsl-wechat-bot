"""
ChatlogService - 聊天记录服务统一接口
"""

from datetime import datetime
from typing import Callable, Optional

from chatlog.wechat_db import ChatRoom, Message, WeChatDBDecryptor, WeChatDBReader, HAS_CRYPTO
from chatlog.summary_service import (
    SummaryConfig, SummaryResult,
    run_chat_summary, start_chat_summary_async, is_summary_running,
)


class ChatlogService:
    """聊天记录服务 - 统一接口"""

    def decrypt(self, input_path: str, key: str, output_path: str) -> int:
        """解密微信数据库，返回解密文件数"""
        decryptor = WeChatDBDecryptor(key)
        return decryptor.decrypt_directory(input_path, output_path)

    def list_groups(self, db_path: str, limit: int = 0) -> list[ChatRoom]:
        """列出所有群聊"""
        reader = WeChatDBReader(db_path)
        try:
            return reader.list_groups(limit=limit)
        finally:
            reader.close()

    def get_messages(
        self,
        db_path: str,
        talker: str,
        *,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        text_only: bool = True,
        limit: int = 100,
    ) -> list[Message]:
        """查询聊天记录"""
        reader = WeChatDBReader(db_path)
        try:
            return reader.get_messages(
                talker=talker,
                start_time=start_time,
                end_time=end_time,
                text_only=text_only,
                limit=limit,
            )
        finally:
            reader.close()

    def get_group_display_name(self, db_path: str, group_id: str) -> str | None:
        """获取群聊显示名"""
        reader = WeChatDBReader(db_path)
        try:
            return reader.get_group_display_name(group_id)
        finally:
            reader.close()

    def run_summary(self, config: SummaryConfig) -> SummaryResult:
        """同步执行群聊总结"""
        return run_chat_summary(config)

    def start_summary_async(
        self,
        config: SummaryConfig,
        on_complete: Callable | None = None,
    ) -> SummaryResult:
        """异步启动群聊总结"""
        return start_chat_summary_async(config, on_complete)

    @property
    def is_summary_running(self) -> bool:
        """总结任务是否正在运行"""
        return is_summary_running()

    @staticmethod
    def has_crypto() -> bool:
        """是否有解密依赖"""
        return HAS_CRYPTO
