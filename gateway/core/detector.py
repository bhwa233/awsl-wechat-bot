"""消息检测器"""
import logging
import queue
import time

from common.config import config
from common.models import GatewayMessage
from gateway.adapters.base import BaseWeChatAdapter
from gateway.core.models import SentMessageStore
from gateway.metrics import metrics

logger = logging.getLogger(__name__)


class MessageDetector:
    """单群消息检测器"""

    def __init__(
        self,
        group_name: str,
        window,
        wechat_adapter: BaseWeChatAdapter,
        *,
        gateway_queue: queue.Queue,
        sent_store: SentMessageStore | None = None,
    ):
        self.group_name = group_name
        self.window = window
        self.adapter = wechat_adapter
        self.gateway_queue = gateway_queue
        self.sent_store = sent_store
        self._running = False
        self._initialized = False

    def stop(self):
        self._running = False

    def run(self):
        """消息检测循环"""
        self._running = True
        logger.info(f"[{self.group_name}] 消息检测线程启动")

        self._initialized = self._init_existing_messages()

        while self._running:
            try:
                if not self.window.Exists(0.5):
                    logger.warning(f"[{self.group_name}] 窗口已关闭,停止监听")
                    break

                if not self._initialized:
                    self._initialized = self._init_existing_messages()
                    time.sleep(config.CHECK_INTERVAL)
                    continue

                self._check_messages()

                time.sleep(config.CHECK_INTERVAL)

            except Exception as e:
                metrics.increment("detector_errors_total", group=self.group_name, reason="loop")
                logger.error(f"[{self.group_name}] 检测出错: {e}")
                time.sleep(1)

        self._running = False
        logger.info(f"[{self.group_name}] 消息检测线程退出")

    def _init_existing_messages(self) -> bool:
        """初始化现有消息基线"""
        try:
            messages = self.adapter.get_messages_from_window(self.window)
            self.adapter.select_new_messages(self.window, messages, initialize=True)
            logger.debug(f"[{self.group_name}] 已建立 {len(messages)} 条初始消息基线")
            return True
        except Exception as e:
            metrics.increment("detector_errors_total", group=self.group_name, reason="initialization")
            logger.error(f"[{self.group_name}] 初始化失败: {e}")
            return False

    def _check_messages(self):
        """检查新消息"""
        messages = self.adapter.get_messages_from_window(self.window)

        if config.DEBUG:
            logger.debug(
                f"[{self.group_name}] 消息列表({len(messages)}条): "
                f"{[m[:20]+'...' if len(m) > 20 else m for m in messages]}"
            )

        new_messages = self.adapter.select_new_messages(self.window, messages)
        for i, msg in enumerate(new_messages):
            if config.DEBUG:
                logger.debug(f"[{self.group_name}] 新消息[{i}]: {msg[:30]}...")

            if self.sent_store and self.sent_store.is_self_sent(self.group_name, msg):
                metrics.increment("detected_messages_total", group=self.group_name, result="self_filtered")
                logger.debug(f"[{self.group_name}] 跳过自己发送的消息: {msg[:30]}...")
                continue

            try:
                self.gateway_queue.put_nowait(GatewayMessage(
                    group_name=self.group_name,
                    content=msg,
                    timestamp=time.time(),
                ))
                metrics.increment("detected_messages_total", group=self.group_name, result="queued")
            except queue.Full:
                metrics.increment("detected_messages_total", group=self.group_name, result="queue_full")
                logger.warning(f"[{self.group_name}] gateway 队列已满")
