"""消息检测器"""
import logging
import queue
import time

from common.config import config
from common.models import GatewayMessage
from gateway.adapters.base import BaseWeChatAdapter
from gateway.core.models import SentMessageStore
from gateway.storage.message_db import MessageDatabase

logger = logging.getLogger(__name__)


class MessageDetector:
    """单群消息检测器"""

    CLEANUP_INTERVAL = 100

    def __init__(
        self,
        group_name: str,
        window,
        wechat_adapter: BaseWeChatAdapter,
        db: MessageDatabase,
        *,
        gateway_queue: queue.Queue,
        sent_store: SentMessageStore | None = None,
    ):
        self.group_name = group_name
        self.window = window
        self.adapter = wechat_adapter
        self.db = db
        self.gateway_queue = gateway_queue
        self.sent_store = sent_store
        self._running = False
        self._check_count = 0
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

                self._check_count += 1
                if self._check_count >= self.CLEANUP_INTERVAL:
                    self.db.cleanup_old_hashes(self.group_name)
                    self._check_count = 0

                time.sleep(config.CHECK_INTERVAL)

            except Exception as e:
                logger.error(f"[{self.group_name}] 检测出错: {e}")
                time.sleep(1)

        self._running = False
        logger.info(f"[{self.group_name}] 消息检测线程退出")

    def _init_existing_messages(self) -> bool:
        """标记现有消息为已处理"""
        try:
            messages = self.adapter.get_messages_from_window(self.window)
            for i, _msg in enumerate(messages):
                msg_hash = self.db.hash_message_with_context(
                    messages, i, self.group_name)
                self.db.mark_processed(msg_hash, self.group_name)
            logger.debug(f"[{self.group_name}] 已标记 {len(messages)} 条初始消息")
            return True
        except Exception as e:
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

        messages_to_check = self.adapter.messages_to_check(messages)
        start_index = len(messages) - len(messages_to_check)

        new_messages = []
        for i, msg in enumerate(messages_to_check):
            idx = start_index + i
            msg_hash = self.db.hash_message_with_context(
                messages, idx, self.group_name)
            is_processed = self.db.is_processed(msg_hash, self.group_name)
            is_last = (i == len(messages_to_check) - 1)
            can_trigger = self.adapter.can_trigger_message(messages_to_check, i)

            if config.DEBUG:
                ctx = [
                    messages[j][:15] +
                    '...' if len(messages[j]) > 15 else messages[j]
                    for j in range(max(0, idx - 2), idx)
                ]
                logger.debug(
                    f"[{self.group_name}] [{i}] msg={msg[:30]}... ctx={ctx} "
                    f"hash={msg_hash[-8:]} processed={is_processed} is_last={is_last}"
                )

            if not is_processed:
                new_messages.append((msg, msg_hash, can_trigger))

        for msg, msg_hash, can_trigger in new_messages:
            self.db.mark_processed(msg_hash, self.group_name)

            if not can_trigger:
                if config.DEBUG:
                    logger.debug(
                        f"[{self.group_name}] 非最新消息,跳过触发: {msg[:30]}...")
                continue

            if self.sent_store and self.sent_store.is_self_sent(self.group_name, msg):
                logger.debug(f"[{self.group_name}] 跳过自己发送的消息: {msg[:30]}...")
                continue

            try:
                self.gateway_queue.put_nowait(GatewayMessage(
                    group_name=self.group_name,
                    content=msg,
                    timestamp=time.time(),
                ))
            except queue.Full:
                logger.warning(f"[{self.group_name}] gateway 队列已满")
