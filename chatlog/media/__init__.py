"""Chatlog 富媒体解密 / 解析模块 (仅支持微信 4.x V2 .dat 格式).

- decrypt:  V2 AES-128-ECB + XOR 解密算法
- adapters: v1/v2 数据库访问 adapter (离线解密 sqlite3 vs 在线读密文)
- cache:    sticker/hd_image LRU + emoticon_lookup 进程级缓存
- resolver: 按 msg_type 分发的 MediaResolver
- route:    HTTP 端点共享逻辑 + v1/v2 MediaBackendBinding
"""

from chatlog.media.adapters import (
    MediaAdapter,
    V1SqliteAdapter,
    V2NativeAdapter,
    build_v1_adapter,
    build_v2_adapter,
)
from chatlog.media.decrypt import (
    V2_MAGIC,
    decrypt_v2_dat,
    detect_image_format,
    is_v2_format,
)
from chatlog.media.resolver import MediaResolver, MediaResult

__all__ = [
    "MediaAdapter",
    "MediaResolver",
    "MediaResult",
    "V1SqliteAdapter",
    "V2NativeAdapter",
    "V2_MAGIC",
    "build_v1_adapter",
    "build_v2_adapter",
    "decrypt_v2_dat",
    "detect_image_format",
    "is_v2_format",
]
