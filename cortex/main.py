#!/usr/bin/env python3
"""Cortex 服务独立启动入口"""
import sys
import logging

from common.config import config

log_level = logging.DEBUG if config.DEBUG else logging.INFO
logging.basicConfig(level=log_level, format='%(asctime)s - %(levelname)s - %(message)s')


def main():
    import uvicorn
    from common.logging import setup_uvicorn_logging
    from cortex.runtime import CortexRuntime
    from cortex.asgi import create_app

    cortex_app = CortexRuntime()
    setup_uvicorn_logging()
    uvicorn.run(create_app(cortex_app), host=config.CORTEX_HTTP_HOST, port=config.CORTEX_HTTP_PORT)


if __name__ == "__main__":
    main()
