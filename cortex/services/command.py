"""远程命令服务 - 从 API 加载和执行动态命令"""

import logging

import httpx

from common.config import config
from common.models import MessageType, OutgoingMessage
from cortex.models import CommandMatch

logger = logging.getLogger(__name__)


class CommandService:
    def __init__(self):
        self.api_base_url = config.COMMAND_API_BASE_URL
        self.commands: list[dict] = []
        self.command_keys: list[str] = []
        self._client = httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )

    @staticmethod
    def _normalize_content(resp_type: str, content: str) -> str:
        if resp_type in {"image", "file"} and content.startswith("data:"):
            return content.split(",", 1)[1] if "," in content else content
        return content

    def load_commands(self) -> bool:
        try:
            resp = self._client.get(
                f"{self.api_base_url}/command/hp",
                headers={"accept": "application/json"},
                timeout=30.0,
            )
            resp.raise_for_status()
            all_commands = resp.json()
            self.commands = [cmd for cmd in all_commands if cmd["key"].strip().lower() != "hp"]
            self.command_keys = [cmd["key"] for cmd in self.commands]
            logger.info(f"加载 {len(self.commands)} 个命令: {self.command_keys}")
            return True
        except Exception as e:
            logger.error(f"加载命令列表失败: {e}")
            return False

    def match_command(self, text: str) -> CommandMatch | None:
        text_lower = text.lower().strip()
        for key in sorted(self.command_keys, key=len, reverse=True):
            if text_lower.startswith(key.lower()):
                params = text[len(key):].strip()
                return CommandMatch(key=key, params=params)
        return None

    def execute_command(self, command_key: str, params: str = "", group_name: str = "") -> OutgoingMessage | None:
        try:
            full_command = f"{command_key} {params}" if params else command_key
            resp = self._client.post(
                f"{self.api_base_url}/command",
                json={"command": full_command},
                headers={"accept": "application/json", "Content-Type": "application/json"},
            )
            resp.raise_for_status()

            data = resp.json()
            content = data.get("content", "")
            resp_type = data.get("type", "text")
            filename = data.get("filename")

            try:
                message_type = MessageType(resp_type)
            except ValueError:
                logger.warning(f"命令 {command_key} 返回了不支持的类型: {resp_type}")
                return None
            if message_type == MessageType.FILE and not filename:
                logger.warning(f"命令 {command_key} 返回 file 类型但缺少 filename")
                return None

            content = self._normalize_content(resp_type, content)

            return OutgoingMessage(
                group_name=group_name,
                message_type=message_type,
                message=content,
                filename=filename,
            )
        except Exception as e:
            logger.error(f"执行命令 {command_key} 失败: {e}")
            return None

    def get_help_text(self) -> str:
        if not self.commands:
            return "命令列表为空,请检查网络连接"
        lines = ["可用命令:"]
        for cmd in self.commands:
            lines.append(f"  {cmd['description']}")
        return "\n".join(lines)

    def close(self):
        self._client.close()
