"""后台语音合成队列."""

import logging
import queue
import threading
from collections.abc import Callable

from common.models import MessageType
from gateway.core.models import SendTask
from gateway.utils.voice_synthesis import synthesize_voice

logger = logging.getLogger(__name__)
_STOP_VOICE_QUEUE = object()


class VoiceSynthesisWorker:
    """后台将 voice 任务合成为标准 voice_file 任务."""

    def __init__(
        self,
        on_ready: Callable[[SendTask], bool],
        queue_size: int,
    ):
        self._on_ready = on_ready
        self._queue: queue.Queue[SendTask | object] = queue.Queue(maxsize=queue_size)
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="voice-synthesis-worker")
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        try:
            self._queue.put_nowait(_STOP_VOICE_QUEUE)
        except queue.Full:
            logger.warning("语音合成队列已满,停止信号入队失败,将等待线程按空队列超时退出")
        if self._thread:
            self._thread.join(timeout=2)

    def enqueue(self, task: SendTask) -> bool:
        try:
            self._queue.put_nowait(task)
            return True
        except queue.Full:
            logger.warning(f"[{task.group_name}] 语音合成队列已满,丢弃消息")
            return False

    def _loop(self) -> None:
        while True:
            try:
                task = self._queue.get(timeout=1)
            except queue.Empty:
                if self._running:
                    continue
                break

            if task is _STOP_VOICE_QUEUE:
                self._queue.task_done()
                break

            try:
                if not self._running:
                    continue
                file_base64, filename = synthesize_voice(task.message)
                prepared = task.model_copy(update={
                    "message_type": MessageType.VOICE_FILE,
                    "message": file_base64,
                    "filename": filename,
                })
                if self._running:
                    self._on_ready(prepared)
            except Exception as e:
                logger.error(f"[{task.group_name}] 生成语音失败: {e}")
            finally:
                self._queue.task_done()
