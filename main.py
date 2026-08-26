#!/usr/bin/env python3
"""
AWSL 微信机器人 - 启动 Gateway + Chatlog + Cortex
"""

import asyncio
import logging

import uvicorn

from common.config import config
from common.logging import setup_uvicorn_logging

log_level = logging.DEBUG if config.DEBUG else logging.INFO
logging.basicConfig(level=log_level, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


async def main():
    from chatlog.asgi import create_app as create_chatlog_app
    from cortex.asgi import create_app as create_cortex_app
    from cortex.runtime import CortexRuntime
    from gateway.asgi import create_app as create_gateway_app
    from gateway.runtime import GatewayRuntime

    setup_uvicorn_logging()

    gateway_config = uvicorn.Config(
        create_gateway_app(GatewayRuntime()),
        host=config.HTTP_API_HOST,
        port=config.HTTP_API_PORT,
        log_level="info",
        ws_max_size=config.WS_MAX_SIZE,
    )
    chatlog_config = uvicorn.Config(
        create_chatlog_app(),
        host=config.CHATLOG_HTTP_HOST,
        port=config.CHATLOG_HTTP_PORT,
        log_level="info",
    )
    cortex_config = uvicorn.Config(
        create_cortex_app(CortexRuntime()),
        host=config.CORTEX_HTTP_HOST,
        port=config.CORTEX_HTTP_PORT,
        log_level="info",
    )

    gateway_server = uvicorn.Server(gateway_config)
    chatlog_server = uvicorn.Server(chatlog_config)
    cortex_server = uvicorn.Server(cortex_config)

    chatlog_server.install_signal_handlers = lambda: None
    cortex_server.install_signal_handlers = lambda: None

    logger.info(
        "Gateway :%d + Chatlog :%d + Cortex :%d 启动中",
        config.HTTP_API_PORT, config.CHATLOG_HTTP_PORT, config.CORTEX_HTTP_PORT,
    )
    try:
        await asyncio.gather(
            gateway_server.serve(),
            chatlog_server.serve(),
            cortex_server.serve(),
        )
    except Exception as e:
        logger.error(f"服务异常退出: {e}", exc_info=True)
    finally:
        gateway_server.should_exit = True
        chatlog_server.should_exit = True
        cortex_server.should_exit = True


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("收到停止信号")
