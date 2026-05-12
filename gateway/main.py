#!/usr/bin/env python3
"""Gateway 服务独立启动入口"""
import logging

from common.config import config

log_level = logging.DEBUG if config.DEBUG else logging.INFO
logging.basicConfig(level=log_level, format='%(asctime)s - %(levelname)s - %(message)s')


def main():
    import uvicorn

    from common.logging import setup_uvicorn_logging
    from gateway.asgi import create_app
    from gateway.runtime import GatewayRuntime

    setup_uvicorn_logging()
    uvicorn.run(create_app(GatewayRuntime()), host=config.HTTP_API_HOST, port=config.HTTP_API_PORT)


if __name__ == "__main__":
    main()
