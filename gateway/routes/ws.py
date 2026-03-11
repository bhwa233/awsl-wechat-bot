"""WebSocket 路由"""

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import ValidationError

from common.config import config
from gateway.core.gateway import Gateway
from common.models import (
    WSSubscribe,
    WSUnsubscribe,
    WSSendMessage,
    WSSubscribeOk,
    WSSendOk,
    WSError,
    parse_client_message,
)

logger = logging.getLogger(__name__)


def create_routes(gateway: Gateway) -> APIRouter:
    router = APIRouter()

    @router.websocket("/ws")
    async def websocket_endpoint(ws: WebSocket):
        if config.HTTP_API_TOKEN:
            token = ws.query_params.get("token", "")
            if token != config.HTTP_API_TOKEN:
                await ws.close(code=4001, reason="Unauthorized")
                return

        await ws.accept()
        gateway.set_event_loop(asyncio.get_running_loop())
        gateway.ws_connect(ws)
        try:
            while True:
                raw = await ws.receive_text()
                await _handle_message(ws, gateway, raw)
        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.debug("WS 连接异常: %s", e)
        finally:
            gateway.ws_disconnect(ws)

    return router


async def _handle_message(ws: WebSocket, gateway: Gateway, raw: str):
    """解析并分发 WS 消息（通过 Pydantic discriminated union 一步解析）"""
    try:
        msg = parse_client_message(raw)
    except (ValueError, ValidationError) as e:
        await ws.send_text(WSError(detail=str(e)).model_dump_json())
        return

    match msg:
        case WSSubscribe():
            gateway.ws_subscribe(ws, msg.group_filter, msg.message_filter)
            await ws.send_text(WSSubscribeOk(
                request_id=msg.request_id,
                group_filter=msg.group_filter,
                message_filter=msg.message_filter,
            ).model_dump_json())

        case WSUnsubscribe():
            gateway.ws_unsubscribe(ws)

        case WSSendMessage():
            is_image = bool(msg.image_base64)
            content = msg.image_base64 if is_image else msg.message
            if content:
                gateway.send_message(msg.group_name, content, is_image=is_image)
            await ws.send_text(WSSendOk(
                request_id=msg.request_id,
            ).model_dump_json())
