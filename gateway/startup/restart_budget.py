import json
import math
import os
import tempfile
import time
from pathlib import Path


class RestartBudget:
    def __init__(self, path: Path | None = None):
        self.path = path or Path.home() / '.cache' / 'awsl-wechat-gateway' / 'wechat-restarts.json'

    def claim(self):
        now = time.time()
        try:
            times = json.loads(self.path.read_text())
        except FileNotFoundError:
            times = []
        if (not isinstance(times, list) or len(times) > 3
                or any(type(t) not in {int, float} or not math.isfinite(t) for t in times)):
            raise ValueError("微信重启限流记录无效,停止自动重启")
        times = [t for t in times if t >= now - 3600]
        if len(times) >= 3:
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd, temporary = tempfile.mkstemp(dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w') as file:
                json.dump([*times, now], file)
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)
        return True
