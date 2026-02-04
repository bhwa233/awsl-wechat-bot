"""消息检测器"""
import time
import queue
import logging
from config import config
from src.models.tasks import TriggerType, MessageTask
from src.storage.message_db import MessageDatabase
from src.core.trigger import TriggerMatcher

logger = logging.getLogger(__name__)


class MessageDetector:
    """单群消息检测器"""

    # 每 N 轮检测执行一次清理
    CLEANUP_INTERVAL = 100

    def __init__(
        self,
        group_name: str,
        window,
        wechat_adapter,
        message_queue: queue.Queue,
        db: MessageDatabase,
        trigger_matcher: TriggerMatcher,
    ):
        self.group_name = group_name
        self.window = window
        self.wechat = wechat_adapter
        self.message_queue = message_queue
        self.db = db
        self.trigger_matcher = trigger_matcher
        self._running = False
        self._check_count = 0

    def run(self, running_flag: callable):
        """
        消息检测循环

        Args:
            running_flag: 返回 bool 的函数，用于检查是否继续运行
        """
        logger.info(f"[{self.group_name}] 消息检测线程启动")

        # 初始化：标记当前所有消息为已处理
        self._init_existing_messages()

        while running_flag():
            try:
                # 检查窗口是否仍然存在
                if not self.window.Exists(0.5):
                    logger.warning(f"[{self.group_name}] 窗口已关闭，停止监听")
                    break

                self._check_messages()

                # 低频清理旧哈希，避免多线程锁竞争
                self._check_count += 1
                if self._check_count >= self.CLEANUP_INTERVAL:
                    self.db.cleanup_old_hashes()
                    self._check_count = 0

                time.sleep(config.CHECK_INTERVAL)

            except Exception as e:
                logger.error(f"[{self.group_name}] 检测出错: {e}")
                time.sleep(1)

        logger.info(f"[{self.group_name}] 消息检测线程退出")

    def _init_existing_messages(self):
        """标记现有消息为已处理"""
        try:
            messages = self.wechat.get_messages_from_window(self.window)
            for i, msg in enumerate(messages):
                msg_hash = self.db.hash_message_with_context(messages, i, self.group_name)
                self.db.mark_processed(msg_hash, self.group_name)
            logger.debug(f"[{self.group_name}] 已标记 {len(messages)} 条初始消息")
        except Exception as e:
            logger.error(f"[{self.group_name}] 初始化失败: {e}")

    def _check_messages(self):
        """检查新消息"""
        messages = self.wechat.get_messages_from_window(self.window)

        if config.DEBUG:
            logger.debug(
                f"[{self.group_name}] 消息列表({len(messages)}条): "
                f"{[m[:20]+'...' if len(m)>20 else m for m in messages]}"
            )

        # 只检查最后几条消息
        messages_to_check = messages[-3:] if len(messages) > 3 else messages
        start_index = len(messages) - len(messages_to_check)

        new_messages = []
        for i, msg in enumerate(messages_to_check):
            idx = start_index + i
            msg_hash = self.db.hash_message_with_context(messages, idx, self.group_name)
            is_processed = self.db.is_processed(msg_hash, self.group_name)
            is_last = (i == len(messages_to_check) - 1)

            if config.DEBUG:
                ctx = [
                    messages[j][:15] + '...' if len(messages[j]) > 15 else messages[j]
                    for j in range(max(0, idx - 2), idx)
                ]
                logger.debug(
                    f"[{self.group_name}] [{i}] msg={msg[:30]}... ctx={ctx} "
                    f"hash={msg_hash[-8:]} processed={is_processed} is_last={is_last}"
                )

            if not is_processed:
                # 只有最后一条消息才触发，其他只标记
                new_messages.append((msg, msg_hash, is_last))

        # 处理新消息
        for msg, msg_hash, can_trigger in new_messages:
            self.db.mark_processed(msg_hash, self.group_name)

            if not can_trigger:
                if config.DEBUG:
                    logger.debug(f"[{self.group_name}] 非最新消息，跳过触发: {msg[:30]}...")
                continue

            trigger_type, content = self.trigger_matcher.match(msg)
            if trigger_type:
                logger.info(f"[{self.group_name}] 检测到触发: {msg}")
                self._enqueue_task(trigger_type, content, msg)

    def _enqueue_task(self, trigger_type: TriggerType, content, original_message: str):
        """将任务加入队列"""
        try:
            task = MessageTask(
                trigger_type=trigger_type,
                group_name=self.group_name,
                window=self.window,
                content=content,
                original_message=original_message,
                timestamp=time.time(),
            )
            self.message_queue.put_nowait(task)
        except queue.Full:
            logger.warning(f"[{self.group_name}] 队列已满，跳过消息")
