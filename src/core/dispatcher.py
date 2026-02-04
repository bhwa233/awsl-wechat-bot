"""任务分发器"""
import time
import queue
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from config import config
from src.models.tasks import TriggerType, MessageTask, SendTask
from src.core.sender import MessageSender

logger = logging.getLogger(__name__)


class TaskDispatcher:
    """任务分发器 - 并发处理，结果送入发送队列"""

    def __init__(
        self,
        message_queue: queue.Queue,
        sender: MessageSender,
        ai_service=None,
        command_service=None,
    ):
        self.message_queue = message_queue
        self.sender = sender
        self.ai_service = ai_service
        self.command_service = command_service

        # 线程池
        self.executor = ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT_TASKS)

        # 群级冷却
        self._cooldown: dict[str, float] = {}
        self._cooldown_lock = threading.Lock()

        self._running = False
        self._thread = None

    def start(self):
        """启动分发线程"""
        self._running = True
        self._thread = threading.Thread(target=self._dispatch_loop, daemon=True)
        self._thread.start()
        logger.info("任务分发线程启动")

    def stop(self):
        """停止分发线程"""
        self._running = False
        self.executor.shutdown(wait=False)
        if self._thread:
            self._thread.join(timeout=2)

    def init_cooldown(self, group_name: str):
        """初始化群冷却时间"""
        self._cooldown[group_name] = 0

    def _dispatch_loop(self):
        """分发循环"""
        while self._running:
            try:
                task = self.message_queue.get(timeout=1)
                self.executor.submit(self._process_task, task)
                self.message_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"分发任务出错: {e}", exc_info=True)

        logger.info("任务分发线程退出")

    def _process_task(self, task: MessageTask):
        """处理单个任务（在线程池中执行）"""
        group_name = task.group_name
        window = task.window
        trigger_type = task.trigger_type
        content = task.content

        try:
            # 检查窗口
            if not window.Exists(0.5):
                logger.warning(f"[{group_name}] 目标窗口已关闭，跳过消息")
                return

            # 群级冷却
            self._wait_for_cooldown(group_name)

            # 处理不同类型
            if trigger_type == TriggerType.TEXT:
                self.sender.enqueue(SendTask(group_name, content, is_image=False))

            elif trigger_type == TriggerType.IMAGE:
                self.sender.enqueue(SendTask(group_name, content, is_image=True))

            elif trigger_type == TriggerType.COMMAND and self.command_service:
                logger.info(f"[{group_name}] 执行命令: {content[0]}")
                res = self.command_service.execute_command(content[0], content[1])
                if res:
                    is_image = res.get("type") == "image"
                    self.sender.enqueue(SendTask(group_name, res["content"], is_image=is_image))

            elif trigger_type == TriggerType.COMMAND_REFRESH:
                logger.info(f"[{group_name}] 刷新命令列表")
                if self.command_service and self.command_service.load_commands():
                    self.sender.enqueue(SendTask(group_name, "已经成功了", is_image=False))

            elif trigger_type == TriggerType.AI and self.ai_service:
                logger.info(f"[{group_name}] AI回复: {content}")
                ans = self.ai_service.ask(content)
                reply = ans if ans else "抱歉，我现在无法回答这个问题 😅"
                self.sender.enqueue(SendTask(group_name, reply, is_image=False))

        except Exception as e:
            logger.error(f"[{group_name}] 处理任务出错: {e}", exc_info=True)

    def _wait_for_cooldown(self, group_name: str):
        """等待群级冷却"""
        wait_time = 0
        with self._cooldown_lock:
            last_time = self._cooldown.get(group_name, 0)
            elapsed = time.time() - last_time
            if elapsed < config.TRIGGER_COOLDOWN:
                wait_time = config.TRIGGER_COOLDOWN - elapsed

        # 在锁外 sleep，不阻塞其他群
        if wait_time > 0:
            logger.debug(f"[{group_name}] 冷却中，等待 {wait_time:.1f} 秒")
            time.sleep(wait_time)

        with self._cooldown_lock:
            self._cooldown[group_name] = time.time()
