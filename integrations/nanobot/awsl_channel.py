"""AWSL Gateway Channel for NanoBot"""
import asyncio
import json
import logging

import websockets

from nanobot.channel.base import BaseChannel
from nanobot.schemas import OutboundMessage

logger = logging.getLogger(__name__)


class AWSlGatewayChannel(BaseChannel):
    """连接 AWSL Gateway WS 的 NanoBot Channel"""

    name = "awsl-gateway"

    def __init__(self, config: dict):
        super().__init__(config)
        self._ws_url = config["ws_url"]  # ws://host:8000/ws?token=xxx
        self._group_filter = config.get("group_filter", "")
        self._message_filter = config.get("message_filter", "")
        self._ws = None

    async def start(self):
        """连接 Gateway WS 并订阅，断线自动重连"""
        while True:
            try:
                async with websockets.connect(self._ws_url) as ws:
                    self._ws = ws
                    await ws.send(json.dumps({
                        "type": "subscribe",
                        "group_filter": self._group_filter,
                        "message_filter": self._message_filter,
                    }))
                    resp = json.loads(await ws.recv())
                    if resp.get("type") != "subscribe_ok":
                        logger.error(f"订阅失败: {resp}")
                        continue
                    logger.info("AWSlGatewayChannel 已连接并订阅")

                    async for raw in ws:
                        data = json.loads(raw)
                        if data.get("type") == "message":
                            self._handle_message(
                                text=data["content"],
                                sender_id=data["group_name"],
                                channel_id=data["group_name"],
                                metadata={"timestamp": data["timestamp"]},
                            )
            except Exception as e:
                logger.warning(f"Gateway WS 断开: {e}, 2s 后重连")
                self._ws = None
                await asyncio.sleep(2)

    async def send(self, msg: OutboundMessage):
        """将 NanoBot 回复发送回 Gateway"""
        if not self._ws:
            logger.warning("WS 未连接，无法发送")
            return
        payload = {
            "type": "send_message",
            "group_name": msg.channel_id,
            "message": msg.text,
        }
        if hasattr(msg, "image_base64") and msg.image_base64:
            payload["image_base64"] = msg.image_base64
        await self._ws.send(json.dumps(payload))
