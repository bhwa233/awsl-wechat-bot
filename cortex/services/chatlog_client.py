"""Chatlog 服务 HTTP 客户端"""
from __future__ import annotations

import logging
from datetime import datetime

import httpx

logger = logging.getLogger(__name__)


class ChatlogClient:
    """调用独立 chatlog 服务的 HTTP 客户端"""

    def __init__(self, base_url: str, token: str = "", timeout: float = 30.0):
        self._base_url = base_url.rstrip("/")
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self._client = httpx.Client(
            base_url=self._base_url,
            headers=headers,
            timeout=httpx.Timeout(connect=5.0, read=timeout, write=10.0, pool=5.0),
        )

    def close(self) -> None:
        self._client.close()

    def decrypt(self) -> dict:
        """触发 chatlog 服务解密微信数据库(路径/密钥由 chatlog 服务自身的 .env 提供)"""
        resp = self._client.post("/api/chatlog/decrypt", timeout=300)
        resp.raise_for_status()
        return resp.json()

    def list_groups(self, limit: int = 0) -> list[dict]:
        resp = self._client.get("/api/chatlog/groups", params={"limit": limit})
        resp.raise_for_status()
        return resp.json()

    def get_messages(
        self,
        group: str,
        *,
        start: datetime | str | None = None,
        end: datetime | str | None = None,
        limit: int = 100,
        desc: bool = False,
    ) -> list[dict]:
        params: dict = {"group": group, "limit": limit}
        if start:
            params["start"] = start.strftime("%Y-%m-%d %H:%M:%S") if isinstance(start, datetime) else start
        if end:
            params["end"] = end.strftime("%Y-%m-%d %H:%M:%S") if isinstance(end, datetime) else end
        if desc:
            params["desc"] = "true"
        resp = self._client.get("/api/chatlog/messages", params=params)
        resp.raise_for_status()
        return resp.json()

    def get_contacts(self, usernames: list[str]) -> list[dict]:
        """批量查询联系人/群成员信息(含头像 URL)"""
        if not usernames:
            return []
        resp = self._client.get(
            "/api/chatlog/contacts",
            params={"usernames": ",".join(usernames)},
        )
        resp.raise_for_status()
        return resp.json()

    def get_room_members(self, group: str) -> list[dict]:
        """列出群成员(群内昵称 + 联系人资料), 群主排首位"""
        resp = self._client.get("/api/chatlog/members", params={"group": group})
        resp.raise_for_status()
        return resp.json()

    def get_group_display_name(self, group: str) -> str | None:
        resp = self._client.get("/api/chatlog/group-display-name", params={"group": group})
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        data = resp.json()
        return data.get("display_name")
