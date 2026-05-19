"""SILK v3 (微信 voice) → MP3.

微信 voice 字节流是 SILK v3 编码 (Skype 开源, 起始 magic `\\x02#!SILK_V3`).
浏览器原生不支持 SILK; 用 pilk → PCM, 再用 av (ffmpeg) 封 MP3.

依赖: uv sync --extra media (pilk + av)
"""

from __future__ import annotations

import io
import logging
import os
import tempfile

logger = logging.getLogger(__name__)

SILK_MAGIC = b"\x02#!SILK_V3"
# 微信 voice 采样率固定 24000 Hz, mono (Skype 标准 SILK widebands)
WX_SILK_SAMPLE_RATE = 24000


def convert_silk_to_mp3(silk_data: bytes) -> bytes | None:
    """SILK v3 → MP3, 失败返回 None."""
    try:
        import pilk
    except ImportError:
        logger.warning("[voice] 缺少 pilk 依赖, 跳过 SILK 转码 (uv sync --extra media 安装)")
        return None
    try:
        import av
    except ImportError:
        logger.warning("[voice] 缺少 av 依赖, 跳过 SILK→MP3 封装")
        return None

    if not silk_data.startswith(SILK_MAGIC):
        logger.warning("[voice] 不是 SILK v3 流 (magic mismatch)")
        return None

    # pilk 要求文件 IO, 落临时文件
    with tempfile.NamedTemporaryFile(suffix=".silk", delete=False) as f_silk:
        f_silk.write(silk_data)
        silk_path = f_silk.name
    pcm_path = silk_path + ".pcm"
    try:
        pilk.decode(silk_path, pcm_path, pcm_rate=WX_SILK_SAMPLE_RATE)
        if not os.path.exists(pcm_path) or os.path.getsize(pcm_path) == 0:
            return None
        return _pcm_to_mp3(pcm_path, WX_SILK_SAMPLE_RATE, av)
    except Exception as exc:
        logger.warning("[voice] SILK 解码失败: %s", exc)
        return None
    finally:
        for p in (silk_path, pcm_path):
            try:
                os.unlink(p)
            except OSError:
                pass


def _pcm_to_mp3(pcm_path: str, sample_rate: int, av) -> bytes | None:
    """16-bit signed mono PCM 文件 → MP3 字节."""
    try:
        out_buf = io.BytesIO()
        out = av.open(out_buf, mode="w", format="mp3")
        out_stream = out.add_stream("mp3", rate=sample_rate)
        out_stream.layout = "mono"

        with open(pcm_path, "rb") as f:
            pcm_data = f.read()

        import numpy as np

        samples = np.frombuffer(pcm_data, dtype=np.int16).reshape(1, -1)
        frame = av.AudioFrame.from_ndarray(samples, format="s16", layout="mono")
        frame.sample_rate = sample_rate
        for packet in out_stream.encode(frame):
            out.mux(packet)
        for packet in out_stream.encode(None):
            out.mux(packet)
        out.close()
        return out_buf.getvalue()
    except Exception as exc:
        logger.warning("[voice] PCM→MP3 封装失败: %s", exc)
        return None
