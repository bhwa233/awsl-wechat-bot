"""Gateway - 纯 WS 消息网关：队列消费 + 广播"""

import asyncio
import logging
import queue
import re
import threading

from fastapi import WebSocket

from gateway.core.sender import MessageSender
from gateway.core.models import SendTask
from common.models import (
    GatewayMessage,
    WSMessageBroadcast,
    WSSubscribe,
    WSSubscription,
)

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
        """注册 WS 连接（无过滤，需 subscribe 后才收消息）"""
        with self._lock:
            self._ws_clients[ws] = WSSubscription()
        logger.info(f"WS 客户端已连接 (当前 {len(self._ws_clients)} 个)")

    def ws_disconnect(self, ws: WebSocket):
        """移除 WS 连接"""
        with self._lock:
            self._ws_clients.pop(ws, None)
        logger.info(f"WS 客户端已断开 (当前 {len(self._ws_clients)} 个)")

    def ws_subscribe(self, ws: WebSocket, msg: WSSubscribe) -> str | None:
        """设置订阅过滤条件，返回错误信息或 None"""
        try:
            sub = WSSubscription(
                subscribed=True,
                group_re=re.compile(msg.group_filter) if msg.group_filter else None,
                message_re=re.compile(msg.message_filter) if msg.message_filter else None,
                priority=msg.priority,
                passive=msg.passive,
            )
        except re.error as e:
            return f"无效的正则表达式: {e}"
        with self._lock:
            if ws in self._ws_clients:
                self._ws_clients[ws] = sub
        logger.info(
            f"WS 客户端已订阅: priority={msg.priority} passive={msg.passive} "
            f"group_filter='{msg.group_filter}' message_filter='{msg.message_filter}'"
        )
        return None

    def ws_unsubscribe(self, ws: WebSocket):
        """清除过滤（不再收消息，但连接保持）"""
        with self._lock:
            if ws in self._ws_clients:
                self._ws_clients[ws] = WSSubscription()

    def send_message(self, group_name: str, content: str, *, is_image: bool = False):
        """发送消息到微信（所有调用者统一入口）"""
        self.sender.enqueue(SendTask(group_name, content, is_image=is_image))

    def _consume_loop(self):
        """消费 gateway_queue，广播到匹配的 WS 客户端"""
        while self._running:
            try:
                msg = self.gateway_queue.get(timeout=1)
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"Gateway 消费出错: {e}", exc_info=True)
                continue

            try:
                self._broadcast(msg)
            except Exception as e:
                logger.error(f"Gateway 广播出错: {e}", exc_info=True)

        logger.info("Gateway 队列消费线程退出")

    def _broadcast(self, msg: GatewayMessage):
        """按优先级广播：passive 客户端始终收到，非 passive 只推送给最高优先级"""
        if not self._loop:
            return

        payload = WSMessageBroadcast(
            group_name=msg.group_name,
            content=msg.content,
            timestamp=msg.timestamp,
        ).model_dump_json()

        with self._lock:
            targets = list(self._ws_clients.items())

        # 分组：passive 始终推送，非 passive 按优先级筛选
        passive_targets = []
        active_targets = []
        for ws, sub in targets:
            if not self._matches(sub, msg.group_name, msg.content):
                continue
            (passive_targets if sub.passive else active_targets).append((ws, sub.priority))

        # 非 passive：只保留最高优先级
        if active_targets:
            max_priority = max(p for _, p in active_targets)
            skipped_priorities = [p for _, p in active_targets if p < max_priority]
            active_targets = [ws for ws, p in active_targets if p == max_priority]
            if skipped_priorities:
                logger.debug(f"[{msg.group_name}] 跳过低优先级: {skipped_priorities} (最高={max_priority})")

        send_to = [ws for ws, _ in passive_targets] + active_targets
        if not send_to:
            return

        logger.info(
            f"[{msg.group_name}] 广播到 {len(send_to)} 个客户端 "
            f"(active={len(active_targets)} passive={len(passive_targets)}): "
            f"{msg.content[:50]}..."
        )
        for ws in send_to:
            try:
                asyncio.run_coroutine_threadsafe(ws.send_text(payload), self._loop)
            except Exception as e:
                logger.warning(f"WS 发送失败: {e}")

    @staticmethod
    def _matches(sub: WSSubscription, group_name: str, content: str) -> bool:
        """检查消息是否匹配订阅过滤条件"""
        if not sub.subscribed:
            return False
        if sub.group_re and not sub.group_re.search(group_name):
            return False
        if sub.message_re and not sub.message_re.search(content):
            return False
        return True
