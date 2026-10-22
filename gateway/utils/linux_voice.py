import base64
import contextlib
import io
import json
import os
import subprocess
import tempfile
import time
import wave
from collections.abc import Callable
from pathlib import Path

import av

from common.config import config


def _pactl(*args):
    result = subprocess.run(["pactl", *args], capture_output=True, text=True, timeout=5, check=True)
    return result.stdout.strip()


@contextlib.contextmanager
def microphone(*, retain_modules: Callable[[], bool] | None = None):
    name = f"awsl_wechat_{os.getpid()}_{time.monotonic_ns()}"
    previous = _pactl("get-default-source")
    source = name + "_mic"
    try:
        _pactl("load-module", "module-null-sink", f"sink_name={name}", "rate=24000", "channels=1")
        _pactl("load-module", "module-remap-source", f"master={name}.monitor",
               f"source_name={source}", "source_properties=device.class=sound")
        _pactl("set-default-source", source)
        yield name, source, previous
    finally:
        if retain_modules is None or not retain_modules():
            cleanup_microphone(source, previous)


def cleanup_microphone(source, previous=None):
    if previous and _pactl("get-default-source") == source:
        _pactl("set-default-source", previous)
    modules = [line.split(None, 2) for line in _pactl("list", "short", "modules").splitlines()]
    sink = source.removesuffix("_mic")
    for token in (f"source_name={source}", f"sink_name={sink}"):
        for module in modules:
            if len(module) == 3 and token in module[2].split():
                _pactl("unload-module", module[0])


def wechat_microphone_streams():
    streams = json.loads(_pactl("--format=json", "list", "source-outputs"))
    matched = []
    for stream in streams:
        properties = stream.get("properties", {})
        binary = properties.get("application.process.binary", "").lower()
        application = properties.get("application.name", "").lower()
        if binary not in {"wechat", "weixin"} and "wechat" not in application and "weixin" not in application:
            continue
        matched.append(stream)
    return matched


def connect_wechat_microphone(source):
    streams = wechat_microphone_streams()
    for stream in streams:
        _pactl("move-source-output", str(stream["index"]), source)
    return bool(streams)


@contextlib.contextmanager
def decoded_voice(file_base64):
    data = base64.b64decode(file_base64, validate=True)
    if len(data) > config.BINARY_MAX_FILE_BYTES:
        raise ValueError("语音文件过大")
    fd, filename = tempfile.mkstemp(prefix="awsl-voice-", suffix=".wav")
    os.close(fd)
    path = Path(filename)
    try:
        samples = 0
        with av.open(io.BytesIO(data)) as container, wave.open(filename, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(24000)
            resampler = av.AudioResampler(format="s16", layout="mono", rate=24000)
            for frame in container.decode(audio=0):
                for converted in resampler.resample(frame):
                    samples += converted.samples
                    if samples / 24000 > config.WINDOWS_VOICE_MAX_DURATION:
                        raise ValueError("语音长度不能超过 60 秒")
                    output.writeframes(converted.to_ndarray().tobytes())
            for converted in resampler.resample(None):
                samples += converted.samples
                output.writeframes(converted.to_ndarray().tobytes())
        duration = samples / 24000
        if duration <= 0 or duration > config.WINDOWS_VOICE_MAX_DURATION:
            raise ValueError("语音长度必须大于 0 且不能超过 60 秒")
        yield path, duration
    finally:
        path.unlink(missing_ok=True)


def play_voice(path: Path, sink: str, *, deadline: float, context_matches: Callable[[], bool]):
    with subprocess.Popen(["paplay", "--latency-msec=20", f"--device={sink}", str(path)],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as player:
        try:
            while player.poll() is None:
                if time.monotonic() >= deadline:
                    raise TimeoutError("播放语音超时")
                if not context_matches():
                    raise RuntimeError("录音期间目标窗口失去焦点")
                time.sleep(.1)
            if player.returncode != 0:
                raise RuntimeError("播放语音失败")
        finally:
            if player.poll() is None:
                player.terminate()
                try:
                    player.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    player.kill()
