"""触发词匹配逻辑"""
from typing import Any

from common.config import config
from cortex.models import TriggerType


class TriggerMatcher:
    """触发词匹配器"""

    def __init__(self, command_service=None, custom_command_service=None):
        self.command_service = command_service
        self.custom_command_service = custom_command_service

    def match(self, text: str) -> tuple[TriggerType | None, Any]:
        """
        检查是否包含触发词

        Returns:
            (TriggerType, content) 或 (None, "") 如果不匹配
        """
        if "animated stickers" in text.lower():
            return (None, "")

        content = text.strip()

        # @BOT_NAMES 触发 -> 作为普通 AI 请求处理
        for bot_name in config.BOT_NAMES:
            if not bot_name:
                continue
            at_pattern = f"@{bot_name}"
            if content.lower().startswith(at_pattern.lower()):
                after_at = content[len(at_pattern):].strip()
                if after_at:
                    return (TriggerType.AI, after_at)
                return (None, "")

        content_lower = content.lower()
        keyword_lower = config.TRIGGER_KEYWORD.lower()

        # 刷新命令(keyword 为空则跳过)
        if keyword_lower and content_lower == f"{keyword_lower} hp":
            return (TriggerType.COMMAND_REFRESH, ("hp", ""))

        # AI 触发(keyword 为空则跳过)
        if keyword_lower and content_lower.startswith(keyword_lower):
            after_keyword = content[len(config.TRIGGER_KEYWORD):].strip()
            if after_keyword:
                return (TriggerType.AI, after_keyword)
            return (None, "")

        # 自定义命令匹配(本地优先,不影响远程命令服务)
        if self.custom_command_service:
            custom_match = self.custom_command_service.match_command(content)
            if custom_match:
                return (TriggerType.CUSTOM_COMMAND, custom_match)

        # 远程命令匹配
        if self.command_service:
            cmd_match = self.command_service.match_command(content)
            if cmd_match:
                return (TriggerType.COMMAND, cmd_match)

        return (None, "")
