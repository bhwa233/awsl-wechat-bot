"""
ChatlogService - 聊天记录服务统一接口
"""

from datetime import datetime

from chatlog.wechat_db import (
    HAS_CRYPTO,
    ChatRoom,
    Contact,
    Message,
    RoomMember,
    WeChatDBDecryptor,
    WeChatDBReader,
)


class ChatlogService:
    """聊天记录服务 - 统一接口"""

    def decrypt(self, input_path: str, key: str, output_path: str) -> int:
        """解密微信数据库,返回解密文件数"""
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
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        text_only: bool = True,
        limit: int = 100,
        offset: int = 0,
        desc: bool = False,
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
                offset=offset,
                desc=desc,
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

    def get_contacts(self, db_path: str, usernames: list[str]) -> list[Contact]:
        """批量查询联系人/群成员信息(含头像 URL)"""
        reader = WeChatDBReader(db_path)
        try:
            return reader.get_contacts(usernames)
        finally:
            reader.close()

    def get_room_members(self, db_path: str, chatroom: str) -> list[RoomMember]:
        """列出群成员(群内昵称 + 联系人资料)"""
        reader = WeChatDBReader(db_path)
        try:
            return reader.get_room_members(chatroom)
        finally:
            reader.close()

    @staticmethod
    def has_crypto() -> bool:
        """是否有解密依赖"""
        return HAS_CRYPTO
