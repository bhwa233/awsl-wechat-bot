"""Cortex 应用 - 业务逻辑中枢"""
import logging
import os
import queue
import threading
import time
from collections import deque

from common.config import config
from common.models import MessageType, OutgoingMessage, WSMessageBroadcast
from cortex.gateway_client import GatewayClient
from cortex.models import MessageListener, TriggerTask, TriggerType
from cortex.services.ai import AIService
from cortex.services.chatlog_client import ChatlogClient
from cortex.services.command import CommandService
from cortex.services.custom_command import CustomCommandService
from cortex.services.summary_service import SummaryService
from cortex.services.trigger import TriggerMatcher

logger = logging.getLogger(__name__)
_STOP_CORTEX_TASK = object()


class CortexRuntime(MessageListener):
    def __init__(self, gateway_ws_url: str | None = None):
        ws_url = gateway_ws_url or f"ws://127.0.0.1:{config.HTTP_API_PORT}/ws"
        self.gateway_client = GatewayClient(ws_url=ws_url)
        self.chatlog_client = self._init_chatlog_client()
        self.summary_service = SummaryService(self.chatlog_client, self.gateway_client)
        self.ai_service = self._init_ai_service()
        self.command_service = self._init_command_service()
        self.custom_command_service = self._init_custom_command_service()
        self.trigger_matcher = TriggerMatcher(self.command_service, self.custom_command_service)
        self._running = False

        self._rate_limit = config.TRIGGER_RATE_LIMIT
        self._rate_window = config.TRIGGER_RATE_WINDOW
        self._trigger_times: dict[str, deque[float]] = {}
        self._task_queue: queue.Queue[TriggerTask | object] = queue.Queue(
            maxsize=config.CORTEX_QUEUE_SIZE
        )
        self._workers: list[threading.Thread] = []

        logger.info("Cortex 初始化完成")

    def _init_chatlog_client(self):
        chatlog_host = config.CHATLOG_HTTP_HOST if config.CHATLOG_HTTP_HOST != "0.0.0.0" else "127.0.0.1"
        chatlog_base_url = f"http://{chatlog_host}:{config.CHATLOG_HTTP_PORT}"
        return ChatlogClient(base_url=chatlog_base_url, token=config.HTTP_API_TOKEN)

    def _init_ai_service(self):
        try:
            return AIService()
        except Exception as e:
            logger.warning(f"AI 服务初始化失败: {e}")
            return None

    def _init_command_service(self):
        if not config.REMOTE_COMMANDS_ENABLED:
            logger.info("远程命令服务已禁用")
            return None
        try:
            service = CommandService()
            if service.load_commands():
                logger.info(f"命令服务初始化成功,已加载 {len(service.commands)} 个命令")
            else:
                logger.warning("命令列表加载失败")
            return service
        except Exception as e:
            logger.warning(f"命令服务初始化失败: {e}")
            return None

    def _init_custom_command_service(self):
        project_root = os.path.dirname(os.path.dirname(__file__))
        return CustomCommandService(
            os.path.join(project_root, "custom_commands.db"),
            summary_service=self.summary_service,
        )

    def start(self):
        self._running = True
        if not self._workers:
            for idx in range(config.CORTEX_POOL_SIZE):
                worker = threading.Thread(
                    target=self._worker_loop,
                    daemon=True,
                    name=f"cortex-worker-{idx + 1}",
                )
                worker.start()
                self._workers.append(worker)
        self.gateway_client.start()
        logger.info("Cortex 已启动")

    def stop(self):
        self._running = False
        self.gateway_client.stop()
        for _ in self._workers:
            try:
                self._task_queue.put_nowait(_STOP_CORTEX_TASK)
            except queue.Full:
                logger.warning("Cortex 处理队列已满,停止信号入队失败")
        for worker in self._workers:
            worker.join(timeout=5)
        self._workers.clear()
        if self.ai_service:
            self.ai_service.close()
        if self.command_service:
            self.command_service.close()
        self.custom_command_service.close()
        self.summary_service.close()
        self.chatlog_client.close()
        logger.info("Cortex 已停止")

    def _is_rate_limited(self, group_name: str) -> bool:
        now = time.time()
        times = self._trigger_times.get(group_name)
        if not times:
            return False
        # 清理窗口外的记录
        cutoff = now - self._rate_window
        while times and times[0] < cutoff:
            times.popleft()
        # 清理空 deque,防止不活跃群组内存泄漏
        if not times:
            del self._trigger_times[group_name]
            return False
        return len(times) >= self._rate_limit

    def _handle_message(self, msg: WSMessageBroadcast) -> None:
        task = self.trigger_matcher.match(msg.content, msg.group_name)
        if not task:
            return

        if self._is_rate_limited(msg.group_name):
            return

        self._trigger_times.setdefault(msg.group_name, deque()).append(time.time())

        try:
            self._task_queue.put_nowait(task)
        except queue.Full:
            logger.warning(f"[{msg.group_name}] Cortex 处理队列已满,丢弃触发任务")

    def _worker_loop(self):
        while True:
            try:
                task = self._task_queue.get(timeout=1)
            except queue.Empty:
                if self._running:
                    continue
                break

            if task is _STOP_CORTEX_TASK:
                self._task_queue.task_done()
                break

            try:
                self._process_and_reply(task)
            finally:
                self._task_queue.task_done()

    def _process_and_reply(self, task: TriggerTask):
        outgoing = self._handle_trigger(task)
        if outgoing:
            self.gateway_client.send_message(outgoing)

    def _handle_trigger(self, task: TriggerTask) -> OutgoingMessage | None:
        msg = None
        message_type = MessageType.TEXT
        filename = None
        group_name = task.group_name

        try:
            if task.trigger_type == TriggerType.AI and self.ai_service:
                logger.info(f"[{group_name}] AI 请求: {task.content[:50]}")
                ans = self.ai_service.ask(task.content)
                msg = ans or "抱歉,我现在无法回答这个问题"

            elif task.trigger_type == TriggerType.COMMAND and self.command_service:
                logger.info(f"[{group_name}] 执行命令: {task.content} {task.params[:50]}...")
                res = self.command_service.execute_command(task.content, task.params, group_name=group_name)
                if res:
                    return res

            elif task.trigger_type == TriggerType.CUSTOM_COMMAND:
                logger.info(f"[{group_name}] 执行自定义命令: {task.content} {task.params[:50]}...")
                res = self.custom_command_service.execute_command(task.content, task.params, group_name=group_name)
                if res:
                    return res

            elif task.trigger_type == TriggerType.CUSTOM_COMMAND_LIST:
                logger.info(f"[{group_name}] 查询自定义命令列表")
                msg = self.custom_command_service.get_list_text(group_name)

            elif task.trigger_type == TriggerType.COMMAND_REFRESH and self.command_service:
                logger.info(f"[{group_name}] 刷新命令列表")
                if self.command_service.load_commands():
                    msg = "已经成功了"

            elif task.trigger_type == TriggerType.TEXT:
                msg = task.content

            elif task.trigger_type == TriggerType.IMAGE:
                msg = task.content
                message_type = MessageType.IMAGE

        except Exception as e:
            logger.error(f"[{group_name}] 处理触发出错: {e}", exc_info=True)
            return None

        if not msg:
            return None

        try:
            return OutgoingMessage(
                group_name=group_name,
                message_type=message_type,
                message=msg,
                filename=filename,
            )
        except Exception as e:
            logger.error(f"[{group_name}] 构造回复消息出错: {e}", exc_info=True)
            return None
