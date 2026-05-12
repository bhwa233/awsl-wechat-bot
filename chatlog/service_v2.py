"""ChatlogServiceV2 - 基于 awsl-wechat-chatlog 原生库的 v2 服务"""

from datetime import datetime
from typing import Optional

from chatlog.wechat_db import ChatRoom, Message
from chatlog.wechat_db_v2 import WeChatDBReaderV2


class ChatlogServiceV2:
    """v2 接口：原生库在线读 SQLCipher v4，不解密落盘。每请求新建 reader，缓存仅请求内有效。"""

    def list_groups(self, encrypted_dir: str, hex_key: str, limit: int = 0) -> list[ChatRoom]:
        return WeChatDBReaderV2(encrypted_dir, hex_key).list_groups(limit=limit)

    def get_messages(
        self,
        encrypted_dir: str,
        hex_key: str,
        talker: str,
        *,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
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

    def get_group_display_name(
        self,
        encrypted_dir: str,
        hex_key: str,
        group_id: str,
    ) -> str | None:
        return WeChatDBReaderV2(encrypted_dir, hex_key).get_group_display_name(group_id)
