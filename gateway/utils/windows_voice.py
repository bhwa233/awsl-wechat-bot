import asyncio
import contextlib
import os
import tempfile

import av
import edge_tts
import numpy as np
import sounddevice as sd

from common.config import config


def prepare_voice(text: str, timeout: float) -> tuple[np.ndarray, int, float]:
    fd, path = tempfile.mkstemp(prefix="awsl-voice-", suffix=".mp3")
    os.close(fd)
    try:
        synthesis = edge_tts.Communicate(text, config.EDGE_TTS_VOICE, rate=config.EDGE_TTS_RATE).save(path)
        asyncio.run(asyncio.wait_for(synthesis, timeout=timeout))
        chunks = []
        with av.open(path) as container:
            resampler = av.AudioResampler(format="flt", layout="mono", rate=config.WINDOWS_VOICE_SAMPLE_RATE)
            for frame in container.decode(audio=0):
                for converted in resampler.resample(frame):
                    chunks.append(converted.to_ndarray().reshape(-1))
            for converted in resampler.resample(None):
                chunks.append(converted.to_ndarray().reshape(-1))
        if not chunks:
            raise RuntimeError("EdgeTTS 未生成音频")

        pcm = np.concatenate(chunks).astype(np.float32)
        duration = len(pcm) / config.WINDOWS_VOICE_SAMPLE_RATE
        if duration > config.WINDOWS_VOICE_MAX_DURATION:
            raise ValueError(f"语音过长: {duration:.1f}s, 上限 {config.WINDOWS_VOICE_MAX_DURATION:.1f}s")

        def matches(actual: str, expected: str) -> bool:
            actual = actual.casefold().strip()
            expected = expected.casefold().strip()
            return actual == expected or actual in expected or expected in actual

        output_device = None
        for index, device in enumerate(sd.query_devices()):
            if device["max_output_channels"] <= 0:
                continue
            if not matches(device["name"], config.WINDOWS_VOICE_PLAYBACK_DEVICE):
                continue
            output_device = index
            if sd.query_hostapis(device["hostapi"])["name"] == "Windows WASAPI":
                break
        if output_device is None:
            raise RuntimeError(f"未找到 VB-CABLE 播放设备: {config.WINDOWS_VOICE_PLAYBACK_DEVICE}")

        default_inputs = {
            hostapi["default_input_device"]
            for hostapi in sd.query_hostapis()
            if hostapi["default_input_device"] >= 0
        }
        if not any(
            matches(sd.query_devices(index)["name"], config.WINDOWS_VOICE_RECORDING_DEVICE)
            for index in default_inputs
        ):
            raise RuntimeError(f"系统默认麦克风不是 VB-CABLE: {config.WINDOWS_VOICE_RECORDING_DEVICE}")
        return pcm, output_device, duration
    finally:
        with contextlib.suppress(OSError):
            os.remove(path)
