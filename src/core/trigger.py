"""触发词匹配逻辑"""
from typing import Any
from config import config
from src.models.tasks import TriggerType


class TriggerMatcher:
    """触发词匹配器"""

    def __init__(self, command_service=None):
        self.command_service = command_service

    def match(self, text: str) -> tuple[TriggerType | None, Any]:
        """
        检查是否包含触发词

        Returns:
            (TriggerType, content) 或 (None, "") 如果不匹配
        """
        if "animated stickers" in text.lower():
            return (None, "")

        content = text.strip()
        content_lower = content.lower()
        keyword_lower = config.TRIGGER_KEYWORD.lower()

        # 刷新命令
        if content_lower == f"{keyword_lower} hp":
            return (TriggerType.COMMAND_REFRESH, ("hp", ""))

        # AI 触发
        if content_lower.startswith(keyword_lower):
            after_keyword = content[len(config.TRIGGER_KEYWORD):].strip()
            if after_keyword:
                return (TriggerType.AI, after_keyword)
            return (None, "")

        # 命令匹配
        if self.command_service:
            cmd_match = self.command_service.match_command(content)
            if cmd_match:
                return (TriggerType.COMMAND, cmd_match)

        return (None, "")
