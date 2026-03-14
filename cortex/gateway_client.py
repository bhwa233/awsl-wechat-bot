"""Gateway WS 客户端 - 纯 WS 连接层

只负责：连接/重连/订阅/收发消息。
业务逻辑通过 MessageListener 交给外部处理。
"""

import asyncio
import logging
import threading
from enum import Enum

import websockets

from common.config import config
from common.models import (
    WSType,
    WSAuth, WSAuthOk,
    WSSubscribe, WSSubscribeOk, WSMessageBroadcast, WSSendMessage, WSError,
    parse_server_message,
)
from cortex.models import MessageListener

logger = logging.getLogger(__name__)

RECONNECT_BASE_DELAY = 1.0
RECONNECT_MAX_DELAY = 30.0
RECONNECT_BACKOFF_FACTOR = 2.0


class ConnectionState(Enum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    SUBSCRIBED = "subscribed"


class GatewayClient:
    """纯 WS 客户端 - 连接 Gateway，收发消息

    生命周期：start() → 自动连接/重连 → stop()
    """

    def __init__(self, ws_url: str):
        self._ws_url = ws_url
        self._listeners: list[MessageListener] = []

        self._state = ConnectionState.DISCONNECTED
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._ws = None

    @property
    def state(self) -> ConnectionState:
        return self._state

    @property
    def connected(self) -> bool:
        return self._state == ConnectionState.SUBSCRIBED

    def add_listener(self, listener: MessageListener):
        self._listeners.append(listener)

    def send_message(self, group_name: str, content: str, *, is_image: bool = False):
        """通过 WS 发送消息到 Gateway（线程安全）"""
        ws, loop = self._ws, self._loop
        if not loop or not ws or self._state != ConnectionState.SUBSCRIBED:
            logger.warning(f"GatewayClient 未连接，无法发送消息到 {group_name}")
            return
        msg = WSSendMessage(
            type=WSType.SEND_MESSAGE,
            group_name=group_name,
            message=None if is_image else content,
            image_base64=content if is_image else None,
        )
        try:
            asyncio.run_coroutine_threadsafe(ws.send(msg.model_dump_json()), loop)
        except RuntimeError:
            logger.warning(f"GatewayClient 事件循环已关闭，消息未发送到 {group_name}")

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True, name="GatewayClient")
        self._thread.start()
        logger.info("GatewayClient 启动")

    def stop(self):
        if self._loop and self._stop_event:
            self._loop.call_soon_threadsafe(self._stop_event.set)
        if self._thread:
            self._thread.join(timeout=5)
        self._state = ConnectionState.DISCONNECTED
        logger.info("GatewayClient 已停止")

    def _run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._stop_event = asyncio.Event()
        try:
            self._loop.run_until_complete(self._connect_loop())
        finally:
            self._loop.close()

    async def _connect_loop(self):
        delay = RECONNECT_BASE_DELAY

        while not self._stop_event.is_set():
            try:
                self._state = ConnectionState.CONNECTING
                async with websockets.connect(self._ws_url) as ws:
                    self._ws = ws
                    self._state = ConnectionState.CONNECTED
                    delay = RECONNECT_BASE_DELAY

                    if not await self._authenticate(ws):
                        self._ws = None
                        continue

                    if not await self._subscribe(ws):
                        self._ws = None
                        continue

                    self._state = ConnectionState.SUBSCRIBED
                    logger.info("GatewayClient 已连接并订阅")
                    await self._receive_loop(ws)
                    self._ws = None

            except asyncio.CancelledError:
                break
            except Exception as e:
                self._ws = None
                if self._stop_event.is_set():
                    break
                self._state = ConnectionState.DISCONNECTED
                logger.warning(f"GatewayClient 连接断开: {e}, {delay:.1f}s 后重连")

                try:
                    await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                    break
                except asyncio.TimeoutError:
                    pass
                delay = min(delay * RECONNECT_BACKOFF_FACTOR, RECONNECT_MAX_DELAY)

        self._state = ConnectionState.DISCONNECTED

    async def _authenticate(self, ws) -> bool:
        """首条消息认证"""
        if not config.HTTP_API_TOKEN:
            return True
        await ws.send(WSAuth(type=WSType.AUTH, token=config.HTTP_API_TOKEN).model_dump_json())
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            msg = parse_server_message(raw)
            if isinstance(msg, WSAuthOk):
                return True
            if isinstance(msg, WSError):
                logger.warning(f"认证失败: {msg.detail}")
            return False
        except asyncio.TimeoutError:
            logger.warning("认证确认超时")
            return False

    async def _subscribe(self, ws) -> bool:
        await ws.send(WSSubscribe(
            type=WSType.SUBSCRIBE,
            group_filter="",
            message_filter="",
            priority=10,
            client_name="cortex",
        ).model_dump_json())

        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            msg = parse_server_message(raw)
            match msg:
                case WSSubscribeOk():
                    return True
                case WSError():
                    logger.warning(f"订阅被拒绝: {msg.detail}")
                case _:
                    logger.warning(f"订阅响应异常: {msg}")
            return False
        except asyncio.TimeoutError:
            logger.warning("订阅确认超时")
            return False

    async def _receive_loop(self, ws):
        async for raw in ws:
            if self._stop_event.is_set():
                await ws.close()
                return
            try:
                msg = parse_server_message(raw)
                if isinstance(msg, WSError):
                    logger.warning(f"Gateway 错误: {msg.detail}")
                elif isinstance(msg, WSMessageBroadcast):
                    for listener in self._listeners:
                        listener.on_message(msg)
            except Exception as e:
                logger.error(f"解析消息异常: {e}", exc_info=True)
