"""Chatlog 服务独立入口"""
import logging

import uvicorn

from chatlog.asgi import create_app
from common.config import config
from common.logging import setup_uvicorn_logging

logger = logging.getLogger(__name__)


def main() -> None:
    log_level = logging.DEBUG if config.DEBUG else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s - %(levelname)s - %(message)s")
    setup_uvicorn_logging()

    logger.info("Chatlog 服务启动 :%d", config.CHATLOG_HTTP_PORT)
    uvicorn.run(
        create_app(),
        host=config.CHATLOG_HTTP_HOST,
        port=config.CHATLOG_HTTP_PORT,
        log_level="info",
    )


if __name__ == "__main__":
    main()
