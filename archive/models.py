"""归档协议:保留原文,消息身份不依赖内容或昵称。"""

import hashlib
import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

MAX_BATCH_BYTES = 1_500_000
MAX_SAFE_INTEGER = 2**53 - 1


class ArchiveMessage(BaseModel):
    talker: str = Field(min_length=1, max_length=200)
    seq: int = Field(ge=0, le=MAX_SAFE_INTEGER)
    local_id: int = Field(ge=0, le=MAX_SAFE_INTEGER)
    server_id: str
    create_time: int = Field(ge=0, le=253402300799)
    sender: str = Field(default="", max_length=200)
    sender_name: str = Field(default="", max_length=1000)
    msg_type: int = Field(ge=0, le=0xFFFFFFFF)
    content: str
    text: str
    is_self: bool
    at_users: list[Annotated[str, Field(max_length=200)]] = Field(default_factory=list, max_length=2000)
    local_key: str

    @field_validator("server_id")
    @classmethod
    def validate_server_id(cls, value):
        if not value.isascii() or not value.isdecimal() or int(value) > 2**64 - 1:
            raise ValueError("Invalid server ID")
        return str(int(value))

    @classmethod
    def from_chatlog(cls, row: dict):
        content = row["content"]
        msg_type = row["msg_type"]
        text = content if msg_type == 1 else ""
        if msg_type == 49:
            try:
                root = ET.fromstring(content)
                text = "\n".join(node.text.strip() for node in root.iter()
                                 if node.tag in {"title", "des", "datadesc", "sourcename"} and node.text and node.text.strip())
            except ET.ParseError:
                pass  # 原文始终保留;无法解析的富媒体不猜测正文。
        identity = [row["seq"], row["local_id"], row["create_time"], msg_type]
        return cls(
            talker=row["talker"], seq=row["seq"], local_id=row["local_id"],
            server_id=str(row.get("server_id_str") or row.get("server_id", 0)),
            create_time=row["create_time"], sender=row["sender"], sender_name=row["sender_name"],
            msg_type=msg_type, content=content, text=text, is_self=row["is_self"],
            at_users=row.get("at_users", []), local_key=digest(identity),
        )


def encode(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def digest(value) -> str:
    return hashlib.sha256(encode(value).encode()).hexdigest()


def state_path(config) -> Path:
    path = Path(config.ARCHIVE_STATE_PATH).expanduser()
    return path if path.is_absolute() else Path(__file__).resolve().parents[1] / path


def config_error(config) -> str:
    if not config.ARCHIVE_ENABLED:
        return "云端归档未启用,请配置 ARCHIVE_ENABLED 并重启服务"
    for key in ("ARCHIVE_URL", "ARCHIVE_TOKEN", "ARCHIVE_ACCOUNT_ID", "ARCHIVE_SOURCE_ID"):
        if not getattr(config, key).strip():
            return f"请配置 {key} 并重启服务"
    try:
        url = urlparse(config.ARCHIVE_URL)
    except ValueError:
        return "ARCHIVE_URL 无效"
    if url.username or url.password or url.query or url.fragment:
        return "ARCHIVE_URL 必须是不含凭证和查询参数的服务地址"
    if any(part in {".", ".."} for part in url.path.split("/")) or "%" in url.path:
        return "ARCHIVE_URL 路径无效"
    if url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}):
        return "ARCHIVE_URL 必须使用 HTTPS(本地回环开发地址除外)"
    if not url.hostname:
        return "ARCHIVE_URL 无效"
    if any(len(getattr(config, key)) > 200 for key in ("ARCHIVE_ACCOUNT_ID", "ARCHIVE_SOURCE_ID")):
        return "归档账号和来源标识最长为 200 字符"
    return ""
