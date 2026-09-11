"""ChatlogServiceV2 - 基于 awsl-wechat-chatlog 原生库的 v2 服务"""

from datetime import datetime

from chatlog.wechat_db import ChatRoom, Contact, Message, RoomMember
from chatlog.wechat_db_v2 import WeChatDBReaderV2


class ChatlogServiceV2:
    """v2 接口:原生库在线读 SQLCipher v4,不解密落盘.每请求新建 reader,缓存仅请求内有效."""

    def list_groups(self, encrypted_dir: str, hex_key: str, limit: int = 0) -> list[ChatRoom]:
        return WeChatDBReaderV2(encrypted_dir, hex_key).list_groups(limit=limit)

    def get_messages(
        self,
        encrypted_dir: str,
        hex_key: str,
        talker: str,
        *,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        text_only: bool = True,
        limit: int = 100,
        offset: int = 0,
        desc: bool = False,
    ) -> list[Message]:
        return WeChatDBReaderV2(encrypted_dir, hex_key).get_messages(
            talker=talker,
            start_time=start_time,
            end_time=end_time,
            text_only=text_only,
            limit=limit,
            offset=offset,
            desc=desc,
        )

    def get_messages_since(
        self,
        encrypted_dir: str,
        hex_key: str,
        items: list[tuple[str, int]],
        limit: int = 500,
    ) -> dict[str, list[Message]]:
        return WeChatDBReaderV2(encrypted_dir, hex_key).get_messages_since(items, limit=limit)

    def search_messages(
        self,
        encrypted_dir: str,
        hex_key: str,
        talker: str,
        *,
        query: str,
        senders: list[str],
        msg_types: list[int] | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
        desc: bool = False,
    ) -> list[Message]:
        return WeChatDBReaderV2(encrypted_dir, hex_key).search_messages(
            talker=talker,
            query=query,
            senders=senders,
            msg_types=msg_types,
            start_time=start_time,
            end_time=end_time,
            limit=limit,
            offset=offset,
            desc=desc,
        )

    def count_messages(
        self,
        encrypted_dir: str,
        hex_key: str,
        talker: str,
        *,
        query: str,
        senders: list[str],
        msg_types: list[int] | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> dict[str, int]:
        return WeChatDBReaderV2(encrypted_dir, hex_key).count_messages(
            talker=talker,
            query=query,
            senders=senders,
            msg_types=msg_types,
            start_time=start_time,
            end_time=end_time,
        )

    def get_group_display_name(
        self,
        encrypted_dir: str,
        hex_key: str,
        group_id: str,
    ) -> str | None:
        return WeChatDBReaderV2(encrypted_dir, hex_key).get_group_display_name(group_id)

    def get_contacts(
        self,
        encrypted_dir: str,
        hex_key: str,
        usernames: list[str],
    ) -> list[Contact]:
        return WeChatDBReaderV2(encrypted_dir, hex_key).get_contacts(usernames)

    def get_room_members(
        self,
        encrypted_dir: str,
        hex_key: str,
        chatroom: str,
    ) -> list[RoomMember]:
        return WeChatDBReaderV2(encrypted_dir, hex_key).get_room_members(chatroom)
