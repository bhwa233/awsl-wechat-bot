import logging
import re
import threading
import time
from collections import deque
from dataclasses import dataclass
from difflib import SequenceMatcher


logger = logging.getLogger(__name__)


@dataclass
class SendTask:
    group_name: str
    content: str
    is_image: bool = False


_WS_RE = re.compile(r'\s+')
_SIMILARITY_THRESHOLD = 0.75  # 相似度阈值
_SIMILARITY_MIN_LEN = 6      # 启用相似度/包含匹配的最小字符数


def _normalize(text: str) -> str:
    """归一化文本：去首尾空白，内部连续空白折叠为单空格"""
    return _WS_RE.sub(' ', text.strip())


def _is_similar(a: str, b: str) -> bool:
    """基于 SequenceMatcher 的相似度判断"""
    if not a or not b:
        return False
    ratio = len(a) / len(b) if len(b) > 0 else 0
    if ratio < 0.5 or ratio > 2.0:
        return False
    return SequenceMatcher(None, a, b).ratio() >= _SIMILARITY_THRESHOLD


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
                    continue
                if gn != group_name:
                    continue
                # 1. 精确匹配（原始 or 归一化）
                if ct == content or ct_norm == norm:
                    return True
                # 2. 前缀匹配（归一化后，阈值 8 字符）
                if len(ct_norm) >= 8 and len(norm) >= 8 and (
                    ct_norm.startswith(norm) or norm.startswith(ct_norm)
                ):
                    return True
                # 3. 包含匹配（UIA 可能返回含发送者名称等额外内容）
                if len(ct_norm) >= _SIMILARITY_MIN_LEN and len(norm) >= _SIMILARITY_MIN_LEN:
                    if ct_norm in norm or norm in ct_norm:
                        return True
                # 4. 相似度匹配（应对 OCR/UIA 识别差异）
                if len(ct_norm) >= _SIMILARITY_MIN_LEN and len(norm) >= _SIMILARITY_MIN_LEN:
                    if _is_similar(ct_norm, norm):
                        return True
            # 未匹配到，输出 debug 日志帮助排查
            logger.debug(
                f"[{group_name}] is_self_sent 未匹配: "
                f"content={norm[:50]!r} records={[(ct_n[:30], f'{now-ts:.0f}s') for ts, gn, _, ct_n in self._records if gn == group_name and now - ts <= self._ttl]}"
            )
            return False
