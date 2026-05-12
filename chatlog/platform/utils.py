"""Chatlog 平台实现共用小工具。"""

from __future__ import annotations

import os


def is_db_storage_dir(path: str) -> bool:
    return os.path.isdir(os.path.join(path, "contact")) and os.path.isdir(
        os.path.join(path, "message")
    )
