"""Chatlog 搜索参数处理。"""

import re
from datetime import datetime

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_@.\-]{1,128}$")
_MAX_LIST_ITEMS = 200
_MAX_QUERY_LENGTH = 256


def validate_usernames(items: list[str], parameter: str) -> list[str]:
    items = [item.strip() for item in items if item.strip()]
    if len(items) > _MAX_LIST_ITEMS:
        raise ValueError(f"{parameter} 一次最多指定 {_MAX_LIST_ITEMS} 个 wxid")
    invalid = next((item for item in items if not _USERNAME_RE.fullmatch(item)), None)
    if invalid is not None:
        raise ValueError(f"{parameter} 包含非法 wxid: {invalid!r}")
    return list(dict.fromkeys(items))


def normalize_query(query: str | None) -> str:
    normalized_query = (query or "").strip()
    if len(normalized_query) > _MAX_QUERY_LENGTH:
        raise ValueError(f"query 最长 {_MAX_QUERY_LENGTH} 个字符")
    return normalized_query


def escape_like_query(query: str) -> str:
    return query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def parse_time_range(start: str | None, end: str | None) -> tuple[datetime | None, datetime | None]:
    start_time = end_time = None
    if start:
        fmt = "%Y-%m-%d %H:%M:%S" if " " in start else "%Y-%m-%d"
        start_time = datetime.strptime(start, fmt)
    if end:
        fmt = "%Y-%m-%d %H:%M:%S" if " " in end else "%Y-%m-%d"
        end_time = datetime.strptime(end, fmt)
        if " " not in end:
            end_time = end_time.replace(hour=23, minute=59, second=59)
    return start_time, end_time


def message_search_expressions(is_chatroom: bool) -> tuple[str, str]:
    """生成与旧群消息 sender:\\n正文解析一致的 SQL sender/content 表达式。"""
    content = "CAST(m.message_content AS TEXT)"
    sender = "COALESCE(n.user_name, '')"
    if not is_chatroom:
        return sender, content

    separator = f"instr({content}, char(58, 10))"
    prefix = f"substr({content}, 1, {separator} - 1)"
    has_sender_prefix = (
        f"{separator} > 0"
        f" AND instr({prefix}, ' ') = 0"
        f" AND instr({prefix}, char(10)) = 0"
    )
    sender = f"CASE WHEN {has_sender_prefix} THEN {prefix} ELSE {sender} END"
    content = (
        f"CASE WHEN {has_sender_prefix}"
        f" THEN substr({content}, {separator} + 2)"
        f" ELSE {content} END"
    )
    return sender, content
