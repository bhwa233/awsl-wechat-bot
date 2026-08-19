"""消息发送器 - 全局限速"""
import logging
import queue
import threading
import time
from typing import Any

from common.config import config
from common.models import MessageType
from gateway.adapters.base import BaseWeChatAdapter
from gateway.core.models import SendTask, SentMessageStore
from gateway.core.voice_synthesis_worker import VoiceSynthesisWorker
from gateway.metrics import metrics

logger = logging.getLogger(__name__)
_STOP_SENDER = object()


class MessageSender:
    """串行消息发送器,支持全局限速"""

    def __init__(self, adapter: BaseWeChatAdapter, group_windows: dict[str, Any],
                 sent_store: SentMessageStore | None = None):
        self.adapter = adapter
        self.group_windows = group_windows
        self.sent_store = sent_store or SentMessageStore()
        self.send_queue: queue.Queue[SendTask | object] = queue.Queue(maxsize=config.SEND_QUEUE_SIZE)
        self.voice_synthesis_worker = (
            VoiceSynthesisWorker(
                self.enqueue,
                config.SEND_QUEUE_SIZE,
            )
            if adapter.SUPPORTS_VOICE
            else None
        )
        self._last_send_time = 0.0
        self._running = False
        self._thread = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="sender")
        self._thread.start()
        if self.voice_synthesis_worker:
            self.voice_synthesis_worker.start()
        logger.info("消息发送线程启动")

    def stop(self):
        self._running = False
        if self.voice_synthesis_worker:
            self.voice_synthesis_worker.stop()
        try:
            self.send_queue.put_nowait(_STOP_SENDER)
        except queue.Full:
            logger.warning("发送队列已满,停止信号入队失败,将等待线程按空队列超时退出")
        if self._thread:
            self._thread.join(timeout=2)

    def enqueue(self, task: SendTask):
        message_type = task.message_type.value
        try:
            self.send_queue.put_nowait(task)
            metrics.increment(
                "send_enqueue_total",
                group=task.group_name,
                message_type=message_type,
                result="accepted",
            )
            return True
        except queue.Full:
            metrics.increment(
                "send_enqueue_total",
                group=task.group_name,
                message_type=message_type,
                result="queue_full",
            )
            logger.warning(f"[{task.group_name}] 发送队列已满,丢弃消息")
            return False

    def _loop(self):
        while True:
            try:
                task = self.send_queue.get(timeout=1)
            except queue.Empty:
                if self._running:
                    continue
                break

            if task is _STOP_SENDER:
                self.send_queue.task_done()
                break

            started = time.perf_counter()
            message_type = task.message_type.value
            send_result = "error"
            try:
                window = self.group_windows.get(task.group_name)
                if not window:
                    send_result = "group_not_found"
                    logger.warning(f"[{task.group_name}] 群组不存在,跳过发送")
                    continue

                if not window.Exists(0.5):
                    send_result = "window_closed"
                    logger.warning(f"[{task.group_name}] 窗口已关闭,跳过发送")
                    continue

                self._wait_for_rate_limit()

                if task.message_type == MessageType.IMAGE:
                    send_ok = self.adapter.send_image_to_window(window, task.message)
                    preview = "[图片]"
                elif task.message_type == MessageType.FILE:
                    if not task.filename:
                        send_result = "invalid"
                        logger.warning(f"[{task.group_name}] 文件消息缺少文件名,跳过发送")
                        continue
                    send_ok = self.adapter.send_file_to_window(window, task.message, task.filename)
                    preview = f"[文件] {task.filename}"
                elif task.message_type == MessageType.VOICE:
                    if self.voice_synthesis_worker and self.voice_synthesis_worker.enqueue(task):
                        send_result = "voice_queued"
                    else:
                        send_result = "queue_full"
                        logger.warning(f"[{task.group_name}] 语音合成任务入队失败")
                    continue
                elif task.message_type == MessageType.VOICE_FILE:
                    send_ok = self.adapter.send_voice_to_window(
                        window,
                        task.message,
                        task.filename,
                    )
                    preview = f"[语音] {task.filename}"
                else:
                    send_ok = self.adapter.send_text_to_window(window, task.message)
                    preview = f"{task.message[:50]}..."

                if send_ok:
                    send_result = "success"
                    self._last_send_time = time.time()
                    if task.message_type == MessageType.TEXT:
                        self.sent_store.record(task.group_name, task.message)
                    elif task.message_type == MessageType.FILE:
                        self.sent_store.record(task.group_name, task.filename)
                    logger.info(f"[{task.group_name}] 已发送: {preview}")
                else:
                    send_result = "failed"
                    logger.warning(f"[{task.group_name}] 发送失败: {preview}")

            except Exception as e:
                logger.error(f"[{task.group_name}] 发送出错: {e}", exc_info=True)
            finally:
                metrics.increment(
                    "sends_total",
                    group=task.group_name,
                    message_type=message_type,
                    result=send_result,
                )
                metrics.observe(
                    "send_duration_seconds",
                    time.perf_counter() - started,
                    group=task.group_name,
                    message_type=message_type,
                    result=send_result,
                )
                self.send_queue.task_done()

    def _wait_for_rate_limit(self):
        elapsed = time.time() - self._last_send_time
        if elapsed < config.GLOBAL_SEND_INTERVAL:
            wait_time = config.GLOBAL_SEND_INTERVAL - elapsed
            logger.debug(f"全局限速等待 {wait_time:.1f} 秒")
            time.sleep(wait_time)
