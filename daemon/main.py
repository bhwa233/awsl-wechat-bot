#!/usr/bin/env python3
"""Daemon 服务独立启动入口"""

import sys
import logging

import uvicorn

from common.config import config
from common.logging import setup_uvicorn_logging
from daemon.routes import app


def main():
    if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    setup_uvicorn_logging()

    logger = logging.getLogger("daemon")
    logger.info(f"daemon 启动中 (host={config.DAEMON_HOST}, port={config.DAEMON_PORT})")
    uvicorn.run(app, host=config.DAEMON_HOST, port=config.DAEMON_PORT)


if __name__ == "__main__":
    main()
