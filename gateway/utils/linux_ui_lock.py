import os
import threading
import time
from pathlib import Path


class LinuxUILock:
    def __init__(self):
        self._lock = threading.RLock()
        self._local = threading.local()

    def __enter__(self):
        import fcntl

        self._lock.acquire()
        depth = getattr(self._local, "depth", 0)
        if depth:
            self._local.depth = depth + 1
            return self
        try:
            directory = Path(os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"))
            if not directory.is_dir():
                directory = Path.home() / ".cache" / "awsl-wechat-gateway"
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            file = (directory / "wx-sync-ui.lock").open("a")
            deadline = time.monotonic() + 10
            while True:
                try:
                    fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        file.close()
                        raise TimeoutError("微信 UI 正由其他进程操作") from None
                    time.sleep(.05)
            self._local.file = file
            self._local.depth = 1
            return self
        except BaseException:
            self._lock.release()
            raise

    def __exit__(self, *_):
        self._local.depth -= 1
        if self._local.depth == 0:
            self._local.file.close()
        self._lock.release()
