"""wxgf (HEVC 裸流) → JPEG 转换.

wxgf 是微信 4.x 用的自有图片容器: `wxgf` magic + ICC profile + HEVC Annex B NAL units.
浏览器原生不支持, 用 PyAV (内嵌 ffmpeg) 解首帧为 JPEG.

依赖 av: `uv sync --extra media`; 未安装时返回 None 让 caller fallback.

算法来自 https://github.com/ylytdeng/wechat-decrypt monitor_web._convert_hevc_to_jpeg.
"""

from __future__ import annotations

import io
import logging

logger = logging.getLogger(__name__)

# HEVC Annex B 起始码 + NAL header: VPS = 40 01, SPS = 42 01
_HEVC_VPS = b"\x00\x00\x00\x01\x40\x01"
_HEVC_SPS = b"\x00\x00\x00\x01\x42\x01"


def convert_wxgf_to_jpeg(wxgf_data: bytes) -> bytes | None:
    """wxgf 字节 → JPEG 字节, 失败返回 None.

    扫描 HEVC VPS/SPS 起始码定位 Annex B 流, PyAV 解首帧, Pillow 输出 JPEG.
    """
    try:
        import av
    except ImportError:
        logger.warning("[wxgf] 缺少 av 依赖, 跳过 HEVC 转码 (uv sync --extra media 安装)")
        return None

    hevc_start = wxgf_data.find(_HEVC_VPS)
    if hevc_start < 0:
        hevc_start = wxgf_data.find(_HEVC_SPS)
    if hevc_start < 0:
        logger.warning("[wxgf] 未找到 HEVC VPS/SPS 起始码")
        return None

    hevc_stream = wxgf_data[hevc_start:]
    try:
        # 用 tmp 文件而非 BytesIO: PyAV 在 BytesIO 上 demux HEVC 首帧常 yield 空白
        # (HEVC IDR + P 帧依赖, 需要 flush 后才能拿到首张实际帧)
        import os
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".h265", delete=False) as tf:
            tf.write(hevc_stream)
            tmp_path = tf.name
        try:
            container = av.open(tmp_path, format="hevc")
            try:
                # 取最后一个 yield 的 frame:
                # PyAV demux+decode 在早期 yield 时给出的 frame 可能还在 reorder buffer 里
                # (HEVC display order != decode order, 首帧落地可能是 reference frame 残留),
                # 完整遍历后取末帧确保拿到 fully reconstructed picture.
                last_frame = None
                for frame in container.decode(video=0):
                    last_frame = frame
                if last_frame is None:
                    return None
                img = last_frame.to_image()
                buf = io.BytesIO()
                img.save(buf, "JPEG", quality=90)
                return buf.getvalue()
            finally:
                container.close()
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    except Exception as exc:
        logger.warning("[wxgf] HEVC→JPEG 解码失败: %s", exc)
        return None
