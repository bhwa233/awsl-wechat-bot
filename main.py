#!/usr/bin/env python3
"""
AWSL 微信机器人 - 支持多平台、多群监听
监控多个群聊，检测到 "awsl" 消息时自动发送随机图片或AI回复
"""

import sys
import logging

from config import config
from src.bot import AWSlBot

# 根据配置设置日志级别
log_level = logging.DEBUG if config.DEBUG else logging.INFO

# 日志配置
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    try:
        bot = AWSlBot()
        bot.run()
    except Exception as e:
        logger.error(f"启动失败: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
