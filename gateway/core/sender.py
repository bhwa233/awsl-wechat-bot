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


class MessageSender:
    """串行消息发送器，支持全局限速"""

    def __init__(self, adapter: BaseWeChatAdapter, group_windows: dict[str, Any],
                 sent_store: SentMessageStore | None = None):
        self.adapter = adapter
        self.group_windows = group_windows
        self.sent_store = sent_store or SentMessageStore()
        self.send_queue: queue.Queue[SendTask] = queue.Queue()
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
        if self._thread:
            self._thread.join(timeout=2)

    def enqueue(self, task: SendTask):
        self.send_queue.put(task)

    def _loop(self):
        while self._running:
            try:
                task = self.send_queue.get(timeout=1)
            except queue.Empty:
                continue

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

                if send_ok:
                    self._last_send_time = time.time()
                    if not task.is_image:
                        self.sent_store.record(task.group_name, task.content)
                    logger.info(f"[{task.group_name}] 消息已发送")
                else:
                    logger.warning(f"[{task.group_name}] 消息发送失败")

            except Exception as e:
                logger.error(f"发送消息出错: {e}", exc_info=True)
            finally:
                self.send_queue.task_done()

    def _wait_for_rate_limit(self):
        elapsed = time.time() - self._last_send_time
        if elapsed < config.GLOBAL_SEND_INTERVAL:
            wait_time = config.GLOBAL_SEND_INTERVAL - elapsed
            logger.debug(f"全局限速等待 {wait_time:.1f} 秒")
            time.sleep(wait_time)
