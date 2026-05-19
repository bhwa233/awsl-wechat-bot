"""HTTP 层 /media 端点共享逻辑.

v1/v2 routes 共享 GET /media 端点; MediaBackendBinding 子类封装两者
在构造 MediaResolver 时的配置差异, register_media_route() 把端点挂到 APIRouter.
"""

from __future__ import annotations

import logging
import os
from abc import ABC, abstractmethod
from functools import cached_property
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from chatlog.media import (
    MediaAdapter,
    MediaResolver,
    build_v1_adapter,
    build_v2_adapter,
)
from chatlog.platform import ChatlogPlatform
from common.auth import verify_token_or_query

logger = logging.getLogger(__name__)

_MIME = {
    "jpg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
    "bmp": "image/bmp",
    "tif": "image/tiff",
    "hevc": "video/hevc",
    "mp4": "video/mp4",
    "mp3": "audio/mpeg",
}


class MediaBackendBinding(ABC):
    """构造 MediaResolver 时 v1/v2 的差异 (DB key 来源、解密落盘 vs 在线读密文).

    持有 platform 实例 + config 直接依赖, 不接受 callable.
    `adapter` 是 cached_property: 单例复用避免 V2 _key_cache 跨请求失效;
    V1SqliteAdapter per-call 建 connection 无持久状态, V2NativeAdapter 内含锁,
    都线程安全.
    """

    backend_tag: str = "Chatlog"

    def __init__(self, platform: ChatlogPlatform, config) -> None:
        self.platform = platform
        self.config = config

    @abstractmethod
    def config_error(self) -> str | None:
        """返回前置配置错误信息, 配置有效返回 None."""

    @abstractmethod
    def build_adapter(self) -> MediaAdapter:
        """构造对应 backend 的 DB 访问 adapter (只被 `adapter` cached_property 调用)."""

    @cached_property
    def adapter(self) -> MediaAdapter:
        return self.build_adapter()

    def wechat_base_dir(self) -> Path:
        """attach / video / voice 媒体本体存放的 xwechat_files/<wxid> 路径.

        v1/v2 公共: db_storage 父级即 wechat_base.
        """
        db_storage = self.platform.resolve_db_storage_dir(self.config.WECHAT_DB_INPUT_PATH)
        return Path(db_storage).parent

    def is_ready(self) -> bool:
        """backend 自身可用性, 子类覆盖."""
        return self.config_error() is None

    def media_health(self) -> dict:
        """媒体能力探测, 给 health 端点用. 依据 IMAGE_AES_KEY + av/pilk 是否安装."""
        ready = self.is_ready()
        has_image_key = bool(self.config.IMAGE_AES_KEY)
        voice_supported = False
        if ready:
            try:
                import av  # noqa: F401
                import pilk  # noqa: F401
                voice_supported = True
            except ImportError:
                voice_supported = False
        return {
            "image": ready and has_image_key,
            "sticker": ready,
            "video_thumb": ready and has_image_key,
            "voice": voice_supported,
        }

    def native_library_path(self) -> str | None:
        """原生库绝对路径, 仅 v2 有, 用于 health 暴露."""
        return None

    @staticmethod
    def unavailable_media_health() -> dict:
        """platform 不支持时的 media 占位 (全 false)."""
        return {"image": False, "sticker": False, "video_thumb": False, "voice": False}


class V2MediaBackendBinding(MediaBackendBinding):
    """v2: 原生库直读密文 db."""

    backend_tag = "ChatlogV2"

    def config_error(self) -> str | None:
        return self.platform.v2_config_error(self.config)

    def build_adapter(self) -> MediaAdapter:
        db_storage = self.platform.resolve_db_storage_dir(self.config.WECHAT_DB_INPUT_PATH)
        return build_v2_adapter(Path(db_storage), self.config.WECHAT_DB_KEY)

    def native_library_path(self) -> str | None:
        return str(self.platform.library_path())


class V1MediaBackendBinding(MediaBackendBinding):
    """v1: 离线解密落盘后 sqlite3 直读."""

    backend_tag = "ChatlogV1"

    def config_error(self) -> str | None:
        detail = self.platform.v1_config_error(self.config)
        if detail:
            return detail
        if not os.path.isdir(self.config.WECHAT_DB_OUTPUT_PATH):
            return f"目录不存在: {self.config.WECHAT_DB_OUTPUT_PATH}"
        return None

    def build_adapter(self) -> MediaAdapter:
        return build_v1_adapter(Path(self.config.WECHAT_DB_OUTPUT_PATH))


def register_media_route(router: APIRouter, binding: MediaBackendBinding) -> None:
    """在 router 注册 GET /media, 用 binding 屏蔽 v1/v2 差异."""

    def _build_resolver(*, need_base: bool) -> MediaResolver:
        """构造 resolver. need_base=True (image/video-thumb) 才求 wechat_base_dir,
        sticker/voice 不依赖 base, 避免不必要 IO + 让 platform 路径错误时仍可用 voice/sticker.
        adapter 走 binding.adapter cached_property, 跨请求复用 (V2 _key_cache 才能生效)."""
        detail = binding.config_error()
        if detail:
            raise HTTPException(status_code=400, detail=detail)
        cfg = binding.config
        base = binding.wechat_base_dir() if need_base else None
        return MediaResolver(
            wechat_base_dir=base,
            adapter=binding.adapter,
            image_aes_key=cfg.IMAGE_AES_KEY or "",
            image_xor_key=cfg.IMAGE_XOR_KEY,
        )

    def _require_image_key() -> None:
        if not binding.config.IMAGE_AES_KEY:
            raise HTTPException(
                status_code=501,
                detail="IMAGE_AES_KEY 未配置, 运行 tools/extract_wx_image_key_*.py 提取后写入 .env",
            )

    @router.get("/media", dependencies=[Depends(verify_token_or_query)])
    def get_media(
        msg_type: int = Query(..., description="3=image, 34=voice, 43=video thumb, 47=sticker"),
        group: str | None = Query(None, description="群 ID 或 wxid (msg_type=3/43 必需)"),
        server_id: str | None = Query(None, description="消息 server_id 字符串 (避免 JS Number 精度丢失)"),
        content: str | None = Query(None, description="原始 content XML (msg_type=47 必需)"),
        hd: bool = Query(False, description="image: 是否拉高清 (默认缩略图; 高清 wxgf 走 av 转码)"),
    ):
        try:
            server_id_int = int(server_id) if server_id is not None else None
        except ValueError as e:
            raise HTTPException(status_code=400, detail="server_id 必须是整数字符串") from e

        try:
            result = None
            if msg_type == 3:
                _require_image_key()
                if not group or server_id_int is None:
                    raise HTTPException(status_code=400, detail="image 需 group + server_id")
                result = _build_resolver(need_base=True).decode_image(group, server_id_int, hd=hd)
            elif msg_type == 43:
                _require_image_key()
                if not group or server_id_int is None:
                    raise HTTPException(status_code=400, detail="video thumb 需 group + server_id")
                result = _build_resolver(need_base=True).decode_video_thumb(group, server_id_int)
            elif msg_type == 34:
                if server_id_int is None:
                    raise HTTPException(status_code=400, detail="voice 需 server_id")
                result = _build_resolver(need_base=False).decode_voice(server_id_int)
            elif msg_type == 47:
                if not content:
                    raise HTTPException(status_code=400, detail="sticker 需 content")
                result = _build_resolver(need_base=False).decode_sticker(content)
            else:
                raise HTTPException(status_code=400, detail=f"不支持 msg_type={msg_type}")
        except HTTPException:
            raise
        except ValueError as e:
            # adapter 参数校验失败 (例如 V2 收到非法字符) → 400
            raise HTTPException(status_code=400, detail=str(e)) from e
        except Exception as e:
            logger.error("[%s] media 解析失败: %s", binding.backend_tag, e, exc_info=True)
            raise HTTPException(status_code=500, detail="解析失败, 查看日志") from e

        if not result:
            raise HTTPException(status_code=404, detail="未找到对应媒体")
        mime = _MIME.get(result.fmt, "application/octet-stream")
        return Response(
            content=result.data,
            media_type=mime,
            headers={
                "X-Media-Md5": result.md5,
                "X-Media-Format": result.fmt,
                # 内容寻址 (md5 不变=同一份), 浏览器/中间层免重发
                "Cache-Control": "private, max-age=86400, immutable",
                "ETag": f'"{result.md5}"',
            },
        )
