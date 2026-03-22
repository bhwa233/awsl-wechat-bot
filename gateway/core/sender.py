"""消息发送器 - 全局限速"""
import time
import queue
import logging
import threading
from typing import Any

from common.config import config
from gateway.adapters.base import BaseWeChatAdapter
from gateway.core.models import SendTask, SentMessageStore

logger = logging.getLogger(__name__)
_STOP_SENDER = object()


class MessageSender:
    """串行消息发送器，支持全局限速"""

    def __init__(self, adapter: BaseWeChatAdapter, group_windows: dict[str, Any],
                 sent_store: SentMessageStore | None = None):
        self.adapter = adapter
        self.group_windows = group_windows
        self.sent_store = sent_store or SentMessageStore()
        self.send_queue: queue.Queue[SendTask | object] = queue.Queue(maxsize=config.SEND_QUEUE_SIZE)
        self._last_send_time = 0.0
        self._running = False
        self._thread = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="sender")
        self._thread.start()
        logger.info("消息发送线程启动")

    def stop(self):
        self._running = False
        try:
            self.send_queue.put_nowait(_STOP_SENDER)
        except queue.Full:
            logger.warning("发送队列已满，停止信号入队失败，将等待线程按空队列超时退出")
        if self._thread:
            self._thread.join(timeout=2)

    def enqueue(self, task: SendTask):
        try:
            self.send_queue.put_nowait(task)
            return True
        except queue.Full:
            logger.warning(f"[{task.group_name}] 发送队列已满，丢弃消息")
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

            try:
                window = self.group_windows.get(task.group_name)
                if not window:
                    logger.warning(f"[{task.group_name}] 群组不存在，跳过发送")
                    continue

                if not window.Exists(0.5):
                    logger.warning(f"[{task.group_name}] 窗口已关闭，跳过发送")
                    continue

                self._wait_for_rate_limit()

                if task.is_image:
                    send_ok = self.adapter.send_image_to_window(window, task.content)
                else:
                    send_ok = self.adapter.send_text_to_window(window, task.content)

                preview = "[图片]" if task.is_image else f"{task.content[:50]}..."
                if send_ok:
                    self._last_send_time = time.time()
                    if not task.is_image:
                        self.sent_store.record(task.group_name, task.content)
                    logger.info(f"[{task.group_name}] 已发送: {preview}")
                else:
                    logger.warning(f"[{task.group_name}] 发送失败: {preview}")

            except Exception as e:
                logger.error(f"[{task.group_name}] 发送出错: {e}", exc_info=True)
            finally:
                self.send_queue.task_done()

    def _wait_for_rate_limit(self):
        elapsed = time.time() - self._last_send_time
        if elapsed < config.GLOBAL_SEND_INTERVAL:
            wait_time = config.GLOBAL_SEND_INTERVAL - elapsed
            logger.debug(f"全局限速等待 {wait_time:.1f} 秒")
            time.sleep(wait_time)
