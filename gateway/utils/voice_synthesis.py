"""平台无关的语音合成工具."""

import asyncio
import base64
import contextlib
import logging
import os
import tempfile

import httpx

from common.config import config

logger = logging.getLogger(__name__)


def synthesize_voice(text: str) -> tuple[str, str]:
    if config.LOCALAI_TTS_API_URL:
        try:
            return _synthesize_localai(text), "tts.mp3"
        except Exception as exc:
            logger.warning("LocalAI TTS 合成失败, 回退 EdgeTTS: %s", exc)
    return _synthesize_edge(text), "tts.mp3"


def _synthesize_localai(text: str) -> str:
    headers = {}
    if config.LOCALAI_TTS_API_KEY:
        headers["Authorization"] = f"Bearer {config.LOCALAI_TTS_API_KEY}"
    response = httpx.post(
        config.LOCALAI_TTS_API_URL,
        headers=headers,
        json={
            "model": config.LOCALAI_TTS_MODEL,
            "voice": config.LOCALAI_TTS_VOICE,
            "input": text,
            "response_format": "mp3",
        },
        timeout=httpx.Timeout(config.LOCALAI_TTS_TIMEOUT, connect=5.0),
    )
    response.raise_for_status()
    content_type = response.headers.get("content-type", "").partition(";")[0].strip().lower()
    if content_type not in {"audio/mpeg", "audio/mp3"} or not response.content:
        raise RuntimeError(f"LocalAI TTS 返回了无效 MP3: {content_type or 'unknown'}")
    if len(response.content) > config.BINARY_MAX_FILE_BYTES:
        raise ValueError(f"LocalAI TTS MP3 不能超过 {config.BINARY_MAX_FILE_BYTES} 字节")
    return base64.b64encode(response.content).decode("ascii")


def _synthesize_edge(text: str) -> str:
    import edge_tts

    fd, path = tempfile.mkstemp(prefix="awsl-voice-", suffix=".mp3")
    os.close(fd)
    try:
        synthesis = edge_tts.Communicate(
            text,
            config.EDGE_TTS_VOICE,
            rate=config.EDGE_TTS_RATE,
        ).save(path)
        asyncio.run(asyncio.wait_for(synthesis, timeout=config.WINDOWS_VOICE_TIMEOUT))
        with open(path, "rb") as f:
            return base64.b64encode(f.read()).decode("ascii")
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)
