import re
import threading
import time
from collections import deque
from dataclasses import dataclass


@dataclass
class SendTask:
    group_name: str
    content: str
    is_image: bool = False


_WS_RE = re.compile(r'\s+')


def _normalize(text: str) -> str:
    """归一化文本：去首尾空白，内部连续空白折叠为单空格"""
    return _WS_RE.sub(' ', text.strip())


class SentMessageStore:
    """记录 bot 最近发送的消息，供 detector 过滤自己发的消息"""

    def __init__(self, ttl: float = 120.0, maxlen: int = 200):
        self._lock = threading.Lock()
        self._records: deque[tuple[float, str, str, str]] = deque(maxlen=maxlen)
        self._ttl = ttl

    def record(self, group_name: str, content: str):
        with self._lock:
            now = time.time()
            while self._records and now - self._records[0][0] > self._ttl:
                self._records.popleft()
            self._records.append((now, group_name, content, _normalize(content)))

    def is_self_sent(self, group_name: str, content: str) -> bool:
        now = time.time()
        norm = _normalize(content)
        if not norm:
            return False
        with self._lock:
            for ts, gn, ct, ct_norm in reversed(self._records):
                if now - ts > self._ttl:
                    break
                if gn != group_name:
                    continue
                # 精确匹配（原始 or 归一化）
                if ct == content or ct_norm == norm:
                    return True
                # 前缀匹配（归一化后，阈值降到 8 字符）
                if len(ct_norm) >= 8 and len(norm) >= 8 and (
                    ct_norm.startswith(norm) or norm.startswith(ct_norm)
                ):
                    return True
            return False
