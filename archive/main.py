"""Windows/Linux 归档进程;与 main.py 共用进程监督,亦可独立运行。"""

import logging
import signal
import sys
import threading
from contextlib import contextmanager

from archive.client import ArchiveClient
from archive.models import config_error, state_path
from archive.store import ArchiveStore
from archive.sync import ArchiveSync
from common.config import config
from cortex.services.chatlog_client import ChatlogClient

logger = logging.getLogger(__name__)


@contextmanager
def single_writer(path):
    # 现有 linux_ui_lock 依赖 Linux 桌面,不能用于跨平台归档;这里仅用标准库锁状态文件的一个字节。
    with open(str(path) + ".lock", "a+b") as handle:
        handle.seek(0)
        handle.write(b"0")
        handle.flush()
        handle.seek(0)
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield  # 关闭文件即释放锁;不删除锁文件,避免锁 inode 更换。


def main():
    logging.basicConfig(level=logging.DEBUG if config.DEBUG else logging.INFO,
                        format="%(asctime)s - %(levelname)s - %(message)s")
    if error := config_error(config):
        raise SystemExit(error)
    path = state_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    signals = [signal.SIGINT, signal.SIGTERM]
    if hasattr(signal, "SIGBREAK"):
        signals.append(signal.SIGBREAK)
    for sig in signals:
        signal.signal(sig, lambda *_: stop.set())
    with single_writer(path):
        store = ArchiveStore(path, config.ARCHIVE_ACCOUNT_ID, config.ARCHIVE_SOURCE_ID)
        host = config.CHATLOG_HTTP_HOST if config.CHATLOG_HTTP_HOST != "0.0.0.0" else "127.0.0.1"
        chatlog = ChatlogClient(f"http://{host}:{config.CHATLOG_HTTP_PORT}", config.HTTP_API_TOKEN)
        cloud = ArchiveClient(config.ARCHIVE_URL, config.ARCHIVE_TOKEN)
        try:
            sync = ArchiveSync(store, chatlog, cloud, config)
            logger.info("归档进程已启动;由 Cortex 云端归档页选择同步群聊")
            while not stop.is_set():
                sync.tick(stop)
                stop.wait(config.ARCHIVE_POLL_SECONDS)
        finally:
            cloud.close()
            chatlog.close()


if __name__ == "__main__":
    main()
