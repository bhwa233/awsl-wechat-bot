"""触发词匹配逻辑"""
from common.config import config
from cortex.models import TriggerTask, TriggerType
from cortex.services.command import CommandService
from cortex.services.custom_command import CustomCommandService


class TriggerMatcher:
    """触发词匹配器"""

    def __init__(
        self,
        command_service: CommandService | None,
        custom_command_service: CustomCommandService,
    ):
        self.command_service = command_service
        self.custom_command_service = custom_command_service

    def match(self, text: str, group_name: str = "") -> TriggerTask | None:
        """
        检查是否包含触发词

        Returns:
            (TriggerType, content) 或 (None, "") 如果不匹配
        """
        if "animated stickers" in text.lower():
            return None

        content = text.strip()

        # @BOT_NAMES 触发 -> 作为普通 AI 请求处理
        for bot_name in config.BOT_NAMES:
            if not bot_name:
                continue
            at_pattern = f"@{bot_name}"
            if content.lower().startswith(at_pattern.lower()):
                after_at = content[len(at_pattern):].strip()
                if after_at:
                    return TriggerTask(
                        trigger_type=TriggerType.AI,
                        group_name=group_name,
                        content=after_at,
                    )
                return None

        content_lower = content.lower()
        keyword_lower = config.TRIGGER_KEYWORD.lower()

        # 刷新命令(keyword 为空则跳过)
        if keyword_lower and content_lower in {
            f"{keyword_lower} refresh",
            f"{keyword_lower} 刷新命令",
        }:
            if not self.command_service:
                return None
            return TriggerTask(
                trigger_type=TriggerType.COMMAND_REFRESH,
                group_name=group_name,
            )

        # 自定义命令列表(keyword 为空则跳过)
        if keyword_lower and content_lower in {
            f"{keyword_lower} 自定义命令",
            f"{keyword_lower} custom commands",
        }:
            return TriggerTask(
                trigger_type=TriggerType.CUSTOM_COMMAND_LIST,
                group_name=group_name,
            )

        # AI 触发(keyword 为空则跳过)
        if keyword_lower and content_lower.startswith(keyword_lower):
            after_keyword = content[len(config.TRIGGER_KEYWORD):].strip()
            if after_keyword:
                return TriggerTask(
                    trigger_type=TriggerType.AI,
                    group_name=group_name,
                    content=after_keyword,
                )
            return None

        # 自定义命令匹配(本地优先,不影响远程命令服务)
        custom_match = self.custom_command_service.match_command(content, group_name)
        if custom_match:
            return TriggerTask(
                trigger_type=TriggerType.CUSTOM_COMMAND,
                content=custom_match.key,
                group_name=group_name,
                params=custom_match.params,
            )

        # 远程命令匹配
        if self.command_service:
            cmd_match = self.command_service.match_command(content)
            if cmd_match:
                return TriggerTask(
                    trigger_type=TriggerType.COMMAND,
                    content=cmd_match.key,
                    group_name=group_name,
                    params=cmd_match.params,
                )

        return None
