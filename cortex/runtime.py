"""Cortex 应用 - 业务逻辑中枢"""
import logging
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor

from common.config import config
from common.models import WSMessageBroadcast
from cortex.gateway_client import GatewayClient
from cortex.models import MessageListener, TriggerType
from cortex.services.ai import AIService
from cortex.services.command import CommandService
from cortex.services.trigger import TriggerMatcher

logger = logging.getLogger(__name__)


class CortexRuntime(MessageListener):
    def __init__(self, gateway_ws_url: str = None):
        ws_url = gateway_ws_url or f"ws://127.0.0.1:{config.HTTP_API_PORT}/ws"

        self.ai_service = self._init_ai_service()
        self.command_service = self._init_command_service()
        self.trigger_matcher = TriggerMatcher(self.command_service)

        self._rate_limit = config.TRIGGER_RATE_LIMIT
        self._rate_window = config.TRIGGER_RATE_WINDOW
        self._trigger_times: dict[str, deque[float]] = {}
        self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="cortex")

        self.gateway_client = GatewayClient(ws_url=ws_url)
        logger.info("Cortex 初始化完成")

    def _init_ai_service(self):
        try:
            return AIService()
        except Exception as e:
            logger.warning(f"AI 服务初始化失败: {e}")
            return None

    def _init_command_service(self):
        try:
            service = CommandService()
            if service.load_commands():
                logger.info(f"命令服务初始化成功，已加载 {len(service.commands)} 个命令")
            else:
                logger.warning("命令列表加载失败")
            return service
        except Exception as e:
            logger.warning(f"命令服务初始化失败: {e}")
            return None

    def start(self):
        self.gateway_client.start()
        logger.info("Cortex 已启动")

    def stop(self):
        self.gateway_client.stop()
        self._executor.shutdown(wait=False)
        if self.ai_service:
            self.ai_service.close()
        if self.command_service:
            self.command_service.close()
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
        # 清理空 deque，防止不活跃群组内存泄漏
        if not times:
            del self._trigger_times[group_name]
            return False
        return len(times) >= self._rate_limit

    def _handle_message(self, msg: WSMessageBroadcast) -> None:
        trigger_type, content = self.trigger_matcher.match(msg.content)
        if not trigger_type:
            return

        if self._is_rate_limited(msg.group_name):
            return

        self._trigger_times.setdefault(msg.group_name, deque()).append(time.time())

        # 阻塞操作（AI/命令）丢到线程池，不阻塞 GatewayClient 消息接收
        self._executor.submit(self._process_and_reply, trigger_type, content, msg.group_name)

    def _process_and_reply(self, trigger_type: TriggerType, content, group_name: str):
        reply_content, is_image = self._handle_trigger(trigger_type, content, group_name)
        if reply_content:
            self.gateway_client.send_message(group_name, reply_content, is_image=is_image)

    def _handle_trigger(self, trigger_type: TriggerType, content, group_name: str) -> tuple[str | None, bool]:
        msg = None
        is_image = False

        try:
            if trigger_type == TriggerType.AI and self.ai_service:
                logger.info(f"[{group_name}] AI 请求: {content[:50]}")
                ans = self.ai_service.ask(content)
                msg = ans or "抱歉，我现在无法回答这个问题"

            elif trigger_type == TriggerType.COMMAND and self.command_service:
                logger.info(f"[{group_name}] 执行命令: {content[0]} {content[1][:50]}...")
                res = self.command_service.execute_command(content[0], content[1])
                if res:
                    msg = res["content"]
                    is_image = res.get("type") == "image"

            elif trigger_type == TriggerType.COMMAND_REFRESH and self.command_service:
                logger.info(f"[{group_name}] 刷新命令列表")
                if self.command_service.load_commands():
                    msg = "已经成功了"

            elif trigger_type == TriggerType.TEXT:
                msg = content

            elif trigger_type == TriggerType.IMAGE:
                msg = content
                is_image = True

        except Exception as e:
            logger.error(f"[{group_name}] 处理触发出错: {e}", exc_info=True)
            return None, False

        return msg, is_image
