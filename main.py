#!/usr/bin/env python3
"""
AWSL 微信机器人 - 启动 Gateway + Cortex
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
    from gateway.runtime import GatewayRuntime
    from gateway.asgi import create_app as create_gateway_app
    from cortex.runtime import CortexRuntime
    from cortex.asgi import create_app as create_cortex_app

    setup_uvicorn_logging()

    gateway_config = uvicorn.Config(
        create_gateway_app(GatewayRuntime()),
        host=config.HTTP_API_HOST,
        port=config.HTTP_API_PORT,
        log_level="info",
    )
    cortex_config = uvicorn.Config(
        create_cortex_app(CortexRuntime()),
        host=config.CORTEX_HTTP_HOST,
        port=config.CORTEX_HTTP_PORT,
        log_level="info",
    )

    gateway_server = uvicorn.Server(gateway_config)
    cortex_server = uvicorn.Server(cortex_config)

    # 只让一个 server 安装信号处理，避免冲突
    cortex_server.install_signal_handlers = lambda: None

    logger.info("Gateway :%d + Cortex :%d 启动中", config.HTTP_API_PORT, config.CORTEX_HTTP_PORT)
    await asyncio.gather(gateway_server.serve(), cortex_server.serve())


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("收到停止信号")
