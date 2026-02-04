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

        # @BOT_NAME 触发 -> 转换为 TRIGGER_KEYWORD + 空格 + 内容，走统一匹配流程
        if config.BOT_NAME:
            at_pattern = f"@{config.BOT_NAME}"
            if content.lower().startswith(at_pattern.lower()):
                after_at = content[len(at_pattern):].strip()
                # 转换为 "a xxx" 格式，继续走后续匹配逻辑
                content = f"a {after_at}"

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
