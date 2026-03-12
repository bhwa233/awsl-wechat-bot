import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass
class SendTask:
    group_name: str
    content: str
    is_image: bool = False


class SentMessageStore:
    """记录 bot 最近发送的消息，供 detector 过滤自己发的消息"""

    def __init__(self, ttl: float = 120.0, maxlen: int = 200):
        self._lock = threading.Lock()
        self._records: deque[tuple[float, str, str]] = deque(maxlen=maxlen)
        self._ttl = ttl

    def record(self, group_name: str, content: str):
        with self._lock:
            now = time.time()
            while self._records and now - self._records[0][0] > self._ttl:
                self._records.popleft()
            self._records.append((now, group_name, content))

    def is_self_sent(self, group_name: str, content: str) -> bool:
        now = time.time()
        min_prefix = 20
        with self._lock:
            for ts, gn, ct in reversed(self._records):
                if now - ts > self._ttl:
                    break
                if gn != group_name:
                    continue
                if ct == content:
                    return True
                if len(ct) >= min_prefix and len(content) >= min_prefix and (
                    ct.startswith(content) or content.startswith(ct)
                ):
                    return True
            return False
