"""微信富媒体定位/解密 (image / sticker / video_thumb).

仅支持微信 4.x V2 .dat 加密格式. 通过 message_resource.db / emoticon.db
把 (group, server_id) 或 sticker md5 → 本地文件路径 → 解密/明文字节.

依赖:
- common.config.IMAGE_AES_KEY / IMAGE_XOR_KEY (V2 .dat 解密)
- common.config.WECHAT_DB_KEY (per-db SQLCipher 密钥, Linux 是 per-db JSON)
- chatlog.platform.get_chatlog_platform() (原生库读 SQLCipher)
"""

from __future__ import annotations

import glob
import hashlib
import logging
import os
import re
import threading
from dataclasses import dataclass
from functools import cached_property
from pathlib import Path

from chatlog.media import cache
from chatlog.media.adapters import MediaAdapter
from chatlog.media.decrypt import decrypt_v2_dat, is_v2_format
from chatlog.media.voice import convert_silk_to_mp3
from chatlog.media.wxgf import convert_wxgf_to_jpeg

logger = logging.getLogger(__name__)

# message_resource.db packed_info protobuf: \x12\x22\x0a\x20 + 32B ASCII md5
_PACKED_MD5_MARKER = b"\x12\x22\x0a\x20"
_MD5_HEX_RE = re.compile(r"[0-9a-fA-F]{32}")
_STICKER_MD5_RE = re.compile(r'<emoji[^>]*\bmd5="([0-9a-f]{32})"')
_STICKER_CDN_RE = re.compile(r'\bcdnurl="([^"]+)"')

_STICKER_FETCH_TIMEOUT = 5.0
_STICKER_MAX_BYTES = 8 * 1024 * 1024  # 8MB, GIF/webp 表情包远小于此
_STICKER_WALL_CLOCK = 10.0  # iter_bytes 总耗时上限 (httpx timeout 是 idle 不是 wall-clock)
# 限制 sticker 外部 fetch 并发, 防止 40 个 anyio 线程 × 16MB(enc+plain) ≈ 640MB 内存峰值
_STICKER_FETCH_SEM = threading.Semaphore(8)


@dataclass
class MediaResult:
    """解密/解析结果."""
    msg_type: int
    fmt: str                # jpg/png/gif/webp/hevc/mp4/mp3
    data: bytes             # 明文字节
    md5: str                # image/video 本地 md5, sticker xml md5, voice server_id 字符串


class MediaResolver:
    """富媒体解析器, adapter 屏蔽 v1/v2 数据库访问差异 (v1 离线解密 sqlite3, v2 原生库 raw SQL).

    wechat_base_dir=None 时仅支持 voice/sticker; image/video-thumb 会 RuntimeError.
    """

    def __init__(
        self,
        wechat_base_dir: str | Path | None,
        adapter: MediaAdapter,
        image_aes_key: str,
        image_xor_key: int = 0x88,
    ):
        # base=None 表示仅 voice/sticker 路径; image/video-thumb 调用时会在 _require_base 报错
        self.base: Path | None = Path(wechat_base_dir) if wechat_base_dir is not None else None
        self.adapter = adapter
        self.aes_key = image_aes_key
        self.xor_key = image_xor_key

    @cached_property
    def attach_root(self) -> Path:
        self._require_base()
        return self.base / "msg" / "attach"

    @cached_property
    def video_root(self) -> Path:
        self._require_base()
        return self.base / "msg" / "video"

    def _require_base(self) -> None:
        if self.base is None:
            raise RuntimeError(
                "internal: MediaResolver(base=None) 仅支持 voice/sticker; "
                "image/video-thumb 必须由 register_media_route 走 need_base=True 路径"
            )

    # ---------- public API ---------- #

    def decode_image(self, group: str, server_id: int, hd: bool = False) -> MediaResult | None:
        """type=3 image: server_id → md5 → .dat → 解密.

        hd=False (默认): 直接走 _t 缩略图 (jpg, 快). 列表渲染用.
        hd=True:  走 _h 高清; 高清是 wxgf 用 PyAV 转 JPEG; 转码失败回退缩略图.
        """
        md5 = self._lookup_md5(group, server_id, local_type=3)
        if not md5:
            return None

        if not hd:
            thumb_path = self._pick_dat(group, md5, prefer_hd=False)
            if not thumb_path:
                return None
            result = self._decrypt_v2(thumb_path, md5, msg_type=3)
            # 缩略图理论可能是 wxgf (hevc), 浏览器不能渲染 → 放弃, 前端 onerror 走 placeholder
            if result and result.fmt == "hevc":
                return None
            return result

        # hd: 先查 negative cache (旧消息 .dat 已清理场景, 短期内不重复 IO)
        if cache.hd_image_negative_get(md5):
            return None

        # hd: 先查 image hd 缓存 (HEVC 重解 ~500ms-1s; AES 解密 jpg/png 也免重复)
        cached = cache.hd_image.get(md5)
        if cached is not None:
            data, fmt = cached
            return MediaResult(msg_type=3, fmt=fmt, data=data, md5=md5)

        # single-flight: 同 md5 并发只跑一次 HEVC 解码
        is_producer, ev = cache.hd_image_singleflight_acquire(md5)
        if not is_producer:
            # 等待 producer 完成, 最多 30s (HEVC 解码 + IO 兜底)
            ev.event.wait(timeout=30.0)
            cached = cache.hd_image.get(md5)
            if cached is not None:
                data, fmt = cached
                return MediaResult(msg_type=3, fmt=fmt, data=data, md5=md5)
            # producer 失败/超时 → 直接放弃, 避免 N 个 waiter 同时重跑 HEVC
            return None

        try:
            hd_path = self._pick_dat(group, md5, prefer_hd=True)
            if not hd_path:
                cache.hd_image_negative_put(md5)
                return None
            result = self._decrypt_v2(hd_path, md5, msg_type=3)
            if result and result.fmt == "hevc":
                jpeg = convert_wxgf_to_jpeg(result.data)
                if jpeg:
                    cache.hd_image.put(md5, (jpeg, "jpg"))
                    return MediaResult(msg_type=3, fmt="jpg", data=jpeg, md5=md5)
                # 转码失败 → 回退缩略图, 同时把 thumb 填进 hd cache 避免下次重跑 HEVC
                thumb_path = self._pick_dat(group, md5, prefer_hd=False)
                if thumb_path and thumb_path != hd_path:
                    fallback = self._decrypt_v2(thumb_path, md5, msg_type=3)
                    if fallback and fallback.fmt != "hevc":
                        cache.hd_image.put(md5, (fallback.data, fallback.fmt))
                        return fallback
                # 无可用 fallback: hevc 原 bytes 浏览器无法渲染, 直接放弃 → 前端 onerror 走 placeholder
                cache.hd_image_negative_put(md5)
                return None
            if result:
                cache.hd_image.put(md5, (result.data, result.fmt))
                return result
            # _decrypt_v2 返 None
            cache.hd_image_negative_put(md5)
            return None
        finally:
            if is_producer:
                cache.hd_image_singleflight_release(md5)

    def decode_video_thumb(self, group: str, server_id: int) -> MediaResult | None:
        """type=43 video 缩略图: server_id → md5 → msg/video/<YM>/<md5>_thumb.jpg (明文)."""
        md5 = self._lookup_md5(group, server_id, local_type=43)
        if not md5:
            return None
        pattern = str(self.video_root / "*" / f"{md5}_thumb.jpg")
        hits = sorted(glob.glob(pattern))
        if not hits:
            return None
        path = hits[0]
        with open(path, "rb") as f:
            data = f.read()
        return MediaResult(msg_type=43, fmt="jpg", data=data, md5=md5)

    def decode_voice(self, server_id: int) -> MediaResult | None:
        """type=34 voice: media_*.db.VoiceInfo[svr_id].voice_data → SILK → MP3.

        WeChat 4.x 在消息量大时会分片 media_0/1/2.db, 按顺序查直到命中.
        VoiceInfo 表 svr_id 全局唯一, 不需要 group 参数.
        """
        media_dbs = self.adapter.available_media_dbs()
        if not media_dbs:
            return None
        server_id = int(server_id)
        blob = None
        for db in media_dbs:
            try:
                rows = self.adapter.query(
                    db,
                    "SELECT voice_data FROM VoiceInfo WHERE svr_id=? LIMIT 1",
                    (server_id,),
                )
            except Exception as exc:
                logger.warning("[voice] 查 %s 失败, 跳过: %s", db.name, exc)
                continue
            if rows and rows[0] and rows[0][0]:
                blob = rows[0][0]
                break
        if blob is None:
            return None
        if isinstance(blob, str):
            import base64
            try:
                blob = base64.b64decode(blob)
            except Exception:
                return None
        if not blob:
            return None
        mp3 = convert_silk_to_mp3(blob)
        if not mp3:
            return None
        return MediaResult(msg_type=34, fmt="mp3", data=mp3, md5=str(server_id))

    def decode_sticker(self, content_xml: str) -> MediaResult | None:
        """type=47 sticker: content XML cdnurl 直链, 必要时查 emoticon.db AES key.

        策略:
        1. XML 自带 cdnurl → HTTP GET 明文 → 检测格式返回
        2. fallback: emoticon.db 查 md5 → (cdn_url, aes_key, encrypt_url) 重试
        """
        md5_m = _STICKER_MD5_RE.search(content_xml)
        if not md5_m:
            return None
        md5 = md5_m.group(1)

        cached = cache.sticker.get(md5)
        if cached is not None:
            data, fmt = cached
            return MediaResult(msg_type=47, fmt=fmt, data=data, md5=md5)

        cdn_m = _STICKER_CDN_RE.search(content_xml)
        cdn_url = cdn_m.group(1) if cdn_m else None

        data, fmt = self._fetch_sticker_from_url(cdn_url) if cdn_url else (None, None)
        # fmt is None 表示拉到 bytes 但格式不识别 (HTML/403 错误页), 仍需走 emoticon.db fallback
        if (data is None or fmt is None) and self.adapter.has(self.adapter.emoticon_db):
            info = cache.emoticon_lookup_for(self.adapter).get(md5)
            if info:
                if info.get("cdn_url") and info["cdn_url"] != cdn_url:
                    data, fmt = self._fetch_sticker_from_url(info["cdn_url"])
                if (data is None or fmt is None) and info.get("encrypt_url") and info.get("aes_key"):
                    data, fmt = self._fetch_sticker_encrypted(info["encrypt_url"], info["aes_key"])
        if data is None or fmt is None:
            return None
        cache.sticker.put(md5, (data, fmt))
        return MediaResult(msg_type=47, fmt=fmt, data=data, md5=md5)

    # ---------- internal: SQL via adapter ---------- #

    def _lookup_md5(self, group: str, server_id: int, local_type: int) -> str | None:
        """通过 MessageResourceInfo 查明文 md5.

        ChatName2Id.user_name → chat_id; (chat_id, message_svr_id, message_local_type) 唯一定位.
        message_local_type 上 32 bit 是 flag, 用 % 2^32 取低位.
        """
        if not self.adapter.has(self.adapter.resource_db):
            return None
        server_id = int(server_id)
        local_type = int(local_type)

        rows = self.adapter.query(
            self.adapter.resource_db,
            "SELECT rowid FROM ChatName2Id WHERE user_name=? LIMIT 1",
            (group,),
        )
        if not rows:
            return None
        chat_id = int(rows[0][0])

        rows = self.adapter.query(
            self.adapter.resource_db,
            "SELECT packed_info FROM MessageResourceInfo "
            "WHERE chat_id=? AND message_svr_id=? "
            "AND (message_local_type=? OR message_local_type % 4294967296=?) "
            "LIMIT 1",
            (chat_id, server_id, local_type, local_type),
        )
        if not rows or not rows[0]:
            return None
        blob = rows[0][0]
        # v2 native lib 把 BLOB 返回为 base64 字符串; v1 sqlite3 直接 bytes
        if isinstance(blob, str):
            import base64
            try:
                blob = base64.b64decode(blob)
            except Exception:
                return None
        return _extract_md5_from_packed_info(blob)

    # ---------- internal: .dat ---------- #

    def _pick_dat(self, group: str, md5: str, prefer_hd: bool) -> Path | None:
        """attach/<md5(group)>/*/Img/<md5>[_h|_t]?.dat — 优先 _h > 标清 > _t."""
        group_hash = hashlib.md5(group.encode()).hexdigest()
        base = self.attach_root / group_hash
        if not base.is_dir():
            return None
        candidates = sorted(glob.glob(str(base / "*" / "Img" / f"{md5}*.dat")))
        if not candidates:
            return None
        # 优先级: _h.dat > 标清 (无后缀) > _t.dat
        rank = {"_h": 0, "": 1, "_t": 2}
        def order(p: str) -> int:
            name = os.path.basename(p)
            stem = name[len(md5):-len(".dat")]
            return rank.get(stem, 9)
        candidates.sort(key=order)
        if not prefer_hd:
            # 反过来选缩略图
            candidates.reverse()
        return Path(candidates[0])

    def _decrypt_v2(self, dat_path: Path, md5: str, msg_type: int) -> MediaResult | None:
        if not is_v2_format(dat_path):
            return None
        out = decrypt_v2_dat(dat_path, self.aes_key, self.xor_key)
        if not out:
            return None
        data, fmt = out
        return MediaResult(msg_type=msg_type, fmt=fmt, data=data, md5=md5)

    # ---------- internal: sticker ---------- #

    def _fetch_sticker_from_url(self, url: str) -> tuple[bytes | None, str | None]:
        import html
        import time

        import httpx
        # content XML 里的 cdnurl 是 HTML 转义过的 (& → &amp;), 需要 unescape
        url = html.unescape(url)
        with _STICKER_FETCH_SEM:
            deadline = time.monotonic() + _STICKER_WALL_CLOCK
            try:
                with httpx.stream("GET", url, timeout=_STICKER_FETCH_TIMEOUT, follow_redirects=True) as r:
                    r.raise_for_status()
                    buf = bytearray()
                    for chunk in r.iter_bytes():
                        buf.extend(chunk)
                        if len(buf) > _STICKER_MAX_BYTES:
                            logger.warning("[sticker] cdn 响应超过 %dB 上限, 截断: %s", _STICKER_MAX_BYTES, url)
                            return None, None
                        if time.monotonic() > deadline:
                            logger.warning("[sticker] cdn 慢速超时 %.1fs, 截断: %s", _STICKER_WALL_CLOCK, url)
                            return None, None
                    data = bytes(buf)
            except Exception as exc:
                logger.warning("[sticker] cdn 下载失败 %s: %s", url, exc)
                return None, None
        return data, _detect_sticker_format(data)

    def _fetch_sticker_encrypted(self, url: str, aes_key_hex: str) -> tuple[bytes | None, str | None]:
        """encrypt_url 拉密文 + AES-CBC(iv=key) 解密 + PKCS7 去填充."""
        import html
        import time

        import httpx
        from Crypto.Cipher import AES
        url = html.unescape(url)
        with _STICKER_FETCH_SEM:
            deadline = time.monotonic() + _STICKER_WALL_CLOCK
            try:
                with httpx.stream("GET", url, timeout=_STICKER_FETCH_TIMEOUT, follow_redirects=True) as r:
                    r.raise_for_status()
                    buf = bytearray()
                    for chunk in r.iter_bytes():
                        buf.extend(chunk)
                        if len(buf) > _STICKER_MAX_BYTES:
                            logger.warning("[sticker] encrypt 响应超过 %dB 上限, 截断: %s", _STICKER_MAX_BYTES, url)
                            return None, None
                        if time.monotonic() > deadline:
                            logger.warning("[sticker] encrypt 慢速超时 %.1fs, 截断: %s", _STICKER_WALL_CLOCK, url)
                            return None, None
                    enc = bytes(buf)
                key = bytes.fromhex(aes_key_hex)
                cipher = AES.new(key, AES.MODE_CBC, iv=key)
                data = cipher.decrypt(enc)
                pad = data[-1] if data else 0
                if 1 <= pad <= 16 and data[-pad:] == bytes([pad]) * pad:
                    data = data[:-pad]
            except Exception as exc:
                logger.warning("[sticker] encrypt 解密失败: %s", exc)
                return None, None
        return data, _detect_sticker_format(data)


# ---------- helpers ---------- #


def _extract_md5_from_packed_info(blob: bytes) -> str | None:
    """packed_info protobuf: ... \\x12\\x22\\x0a\\x20 + 32 ASCII hex md5 ..."""
    if not blob:
        return None
    idx = blob.find(_PACKED_MD5_MARKER)
    if idx < 0 or idx + 4 + 32 > len(blob):
        return None
    md5 = blob[idx + 4:idx + 4 + 32]
    try:
        s = md5.decode("ascii")
    except UnicodeDecodeError:
        return None
    # int(s, 16) 接受 '+'/'_'/PEP515 数字分隔/'0x' 前缀, 不是严格 md5 校验; 用 fullmatch 收紧
    return s if _MD5_HEX_RE.fullmatch(s) else None


def _detect_sticker_format(data: bytes) -> str | None:
    if len(data) < 4:
        return None
    if data[:3] == b"\xff\xd8\xff":
        return "jpg"
    if data[:4] == b"\x89PNG":
        return "png"
    if data[:3] == b"GIF":
        return "gif"
    if data[:4] == b"RIFF":
        return "webp"
    if data[:4] in (b"wxgf", b"wxam"):
        return "hevc"
    return None
