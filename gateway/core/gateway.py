"""Gateway - 纯 WS 消息网关:队列消费 + 广播"""

import asyncio
import logging
import queue
import re
import threading

from fastapi import WebSocket

from common.models import (
    GatewayMessage,
    MessageType,
    WSMessageBroadcast,
    WSSubscribe,
    WSSubscription,
)
from gateway.core.models import SendTask
from gateway.core.sender import MessageSender
from gateway.metrics import metrics

logger = logging.getLogger(__name__)


class Gateway:
    """纯 WS 消息网关 - 队列消费 + 广播"""

    def __init__(self, sender: MessageSender):
        self.sender = sender
        self.gateway_queue: queue.Queue[GatewayMessage] = queue.Queue(maxsize=100)
        self._ws_clients: dict[WebSocket, WSSubscription] = {}
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self):
        """启动 sender + 队列消费线程"""
        self._running = True
        self.sender.start()
        self._thread = threading.Thread(target=self._consume_loop, daemon=True)
        self._thread.start()
        logger.info("Gateway 队列消费线程启动")

    def stop(self):
        """停止"""
        self._running = False
        self.sender.stop()
        if self._thread:
            self._thread.join(timeout=2)

    def set_event_loop(self, loop: asyncio.AbstractEventLoop):
        """WS 端点连接时设置 uvicorn 事件循环"""
        self._loop = loop

    def ws_connect(self, ws: WebSocket):
        """注册 WS 连接(无过滤,需 subscribe 后才收消息)"""
        with self._lock:
            self._ws_clients[ws] = WSSubscription()
            connection_count = len(self._ws_clients)
        metrics.set_value("websocket_connections", connection_count)
        logger.info(f"WS 客户端已连接 (当前 {len(self._ws_clients)} 个)")

    def ws_disconnect(self, ws: WebSocket):
        """移除 WS 连接"""
        with self._lock:
            self._ws_clients.pop(ws, None)
            connection_count = len(self._ws_clients)
            subscription_count = sum(subscription.subscribed for subscription in self._ws_clients.values())
        metrics.set_value("websocket_connections", connection_count)
        metrics.set_value("websocket_subscriptions", subscription_count)
        logger.info(f"WS 客户端已断开 (当前 {len(self._ws_clients)} 个)")

    def ws_subscribe(self, ws: WebSocket, msg: WSSubscribe) -> str | None:
        """设置订阅过滤条件,返回错误信息或 None"""
        try:
            sub = WSSubscription.from_subscribe(msg)
        except re.error as e:
            return f"无效的正则表达式: {e}"
        with self._lock:
            if ws in self._ws_clients:
                self._ws_clients[ws] = sub
            subscription_count = sum(subscription.subscribed for subscription in self._ws_clients.values())
        metrics.set_value("websocket_subscriptions", subscription_count)
        name = msg.client_name or "anonymous"
        logger.info(
            f"WS 客户端已订阅 [{name}]: priority={msg.priority} passive={msg.passive} "
            f"group_filter='{msg.group_filter}' message_filter='{msg.message_filter}'"
        )
        return None

    def ws_unsubscribe(self, ws: WebSocket):
        """清除过滤(不再收消息,但连接保持)"""
        with self._lock:
            if ws in self._ws_clients:
                self._ws_clients[ws] = WSSubscription()
            subscription_count = sum(subscription.subscribed for subscription in self._ws_clients.values())
        metrics.set_value("websocket_subscriptions", subscription_count)

    def send_message(self, task: SendTask) -> bool:
        """发送消息到微信(所有调用者统一入口)"""
        return self.sender.enqueue(task)

    def get_send_error(self, task: SendTask) -> str | None:
        """返回当前平台无法发送该消息的原因。"""
        if task.message_type in {MessageType.VOICE, MessageType.VOICE_FILE} and not self.sender.adapter.SUPPORTS_VOICE:
            return "当前平台不支持语音发送"
        return None

    def _consume_loop(self):
        """消费 gateway_queue,广播到匹配的 WS 客户端"""
        while self._running:
            try:
                msg = self.gateway_queue.get(timeout=1)
            except queue.Empty:
                continue
            except Exception as e:
                metrics.increment("broadcast_messages_total", result="error")
                logger.error(f"Gateway 消费出错: {e}", exc_info=True)
                continue

            try:
                self._broadcast(msg)
            except Exception as e:
                metrics.increment("broadcast_messages_total", result="error")
                logger.error(f"Gateway 广播出错: {e}", exc_info=True)

        logger.info("Gateway 队列消费线程退出")

    def _broadcast(self, msg: GatewayMessage):
        """按优先级广播:passive 客户端始终收到,非 passive 只推送给最高优先级"""
        if not self._loop:
            metrics.increment("broadcast_messages_total", result="no_event_loop")
            return

        payload = WSMessageBroadcast(
            group_name=msg.group_name,
            content=msg.content,
            timestamp=msg.timestamp,
        ).model_dump_json()

        with self._lock:
            targets = list(self._ws_clients.items())

        # 分组:passive 始终推送,非 passive 按优先级筛选
        passive_matched = []
        active_matched = []
        for ws, sub in targets:
            if not self._matches(sub, msg.group_name, msg.content):
                continue
            (passive_matched if sub.passive else active_matched).append((ws, sub))

        # 非 passive:只保留最高优先级
        if active_matched:
            max_priority = max(sub.priority for _, sub in active_matched)
            skipped = [(ws, sub) for ws, sub in active_matched if sub.priority < max_priority]
            active_matched = [(ws, sub) for ws, sub in active_matched if sub.priority == max_priority]
            if skipped:
                skipped_names = [f"{sub.client_name or 'anonymous'}({sub.priority})" for _, sub in skipped]
                logger.debug(
                    f"[{msg.group_name}] 跳过低优先级: {skipped_names} (最高={max_priority})"
                )

        send_to = passive_matched + active_matched
        if not send_to:
            metrics.increment("broadcast_messages_total", result="no_subscriber")
            return

        names = [sub.client_name or "anonymous" for _, sub in send_to]
        content_preview = msg.content[:50] + "..." if len(msg.content) > 50 else msg.content
        logger.debug(
            f"[{msg.group_name}] 广播到 {names}: {content_preview}"
        )
        scheduled_count = 0
        for ws, _ in send_to:
            try:
                asyncio.run_coroutine_threadsafe(ws.send_text(payload), self._loop)
                scheduled_count += 1
                metrics.increment("broadcast_deliveries_total")
            except Exception as e:
                logger.warning(f"WS 发送失败: {e}")
        result = "scheduled" if scheduled_count else "schedule_error"
        metrics.increment("broadcast_messages_total", result=result)

    @staticmethod
    def _matches(sub: WSSubscription, group_name: str, content: str) -> bool:
        """检查消息是否匹配订阅过滤条件"""
        if not sub.subscribed:
            return False
        if sub.group_re and not sub.group_re.search(group_name):
            return False
        return not (sub.message_re and not sub.message_re.search(content))
