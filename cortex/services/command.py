"""远程命令服务 - 从 API 加载和执行动态命令"""

import json
import logging
import os
import threading

import httpx
from pydantic import BaseModel, Field

from common.config import config
from common.models import MessageType, OutgoingMessage
from cortex.models import CommandMatch

logger = logging.getLogger(__name__)


class RemoteCommandPolicyUpdate(BaseModel):
    allowlist: list[str] | str | None = None
    bypass_groups: list[str] | str | None = None


class RemoteCommandInfo(BaseModel):
    key: str
    description: str = ""


class RemoteCommandPolicyResponse(BaseModel):
    allowlist: list[str] = Field(default_factory=list)
    bypass_groups: list[str] = Field(default_factory=list)
    available_commands: list[RemoteCommandInfo] = Field(default_factory=list)
    unrestricted_by_default: bool = True


class CommandService:
    def __init__(self, policy_path: str | None = None):
        self.api_base_url = config.COMMAND_API_BASE_URL
        self.commands: list[dict] = []
        self.command_keys: list[str] = []
        self.policy_path = policy_path
        self._policy_lock = threading.Lock()
        self._allowlist: set[str] = set()
        self._allowlist_items: list[str] = []
        self._bypass_groups: set[str] = set()
        self._client = httpx.Client(
            timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=10.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10),
        )
        self.reload_policy()

    @staticmethod
    def _normalize_content(resp_type: str, content: str) -> str:
        if resp_type in {"image", "file"} and content.startswith("data:"):
            return content.split(",", 1)[1] if "," in content else content
        return content

    def load_commands(self) -> bool:
        try:
            resp = self._client.get(
                f"{self.api_base_url}/command/list",
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

    @staticmethod
    def _normalize_list(value: list[str] | str | None) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            if not value.strip():
                return []
            try:
                items = json.loads(value)
            except json.JSONDecodeError:
                items = [part.strip() for part in value.replace("\n", ",").split(",")]
        else:
            items = value
        if not isinstance(items, list):
            return []
        return [str(item).strip() for item in items if str(item).strip()]

    @classmethod
    def _normalized_names(cls, value: list[str] | str | None) -> set[str]:
        return {name.lower() for name in cls._normalize_list(value)}

    def _available_commands(self) -> list[RemoteCommandInfo]:
        result = []
        for command in self.commands:
            key = str(command.get("key", "")).strip()
            if not key:
                continue
            result.append(RemoteCommandInfo(
                key=key,
                description=str(command.get("description", "") or ""),
            ))
        return result

    def get_policy(self) -> RemoteCommandPolicyResponse:
        with self._policy_lock:
            allowlist = list(self._allowlist_items)
            bypass_groups = sorted(self._bypass_groups)
        return RemoteCommandPolicyResponse(
            allowlist=allowlist,
            bypass_groups=bypass_groups,
            available_commands=self._available_commands(),
            unrestricted_by_default=not allowlist,
        )

    def update_policy(self, data: RemoteCommandPolicyUpdate) -> RemoteCommandPolicyResponse:
        allowlist = self._normalize_list(data.allowlist)
        bypass_groups = self._normalize_list(data.bypass_groups)
        if self.policy_path:
            os.makedirs(os.path.dirname(self.policy_path) or ".", exist_ok=True)
            temp_path = f"{self.policy_path}.tmp.{os.getpid()}"
            with open(temp_path, "w", encoding="utf-8") as file:
                json.dump(
                    {"allowlist": allowlist, "bypass_groups": bypass_groups},
                    file,
                    ensure_ascii=False,
                    indent=2,
                )
                file.write("\n")
            os.replace(temp_path, self.policy_path)
        with self._policy_lock:
            self._allowlist = self._normalized_names(allowlist)
            self._allowlist_items = allowlist
            self._bypass_groups = set(bypass_groups)
        return self.get_policy()

    def reload_policy(self) -> RemoteCommandPolicyResponse:
        if not self.policy_path or not os.path.exists(self.policy_path):
            with self._policy_lock:
                self._allowlist = set()
                self._allowlist_items = []
                self._bypass_groups = set()
            return self.get_policy()
        try:
            with open(self.policy_path, encoding="utf-8") as file:
                data = json.load(file)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("远程命令策略文件读取失败,保留当前策略: %s", e)
            return self.get_policy()
        if not isinstance(data, dict):
            logger.warning("远程命令策略文件格式无效,保留当前策略")
            return self.get_policy()
        with self._policy_lock:
            allowlist = self._normalize_list(data.get("allowlist"))
            self._allowlist = self._normalized_names(allowlist)
            self._allowlist_items = allowlist
            self._bypass_groups = set(self._normalize_list(data.get("bypass_groups")))
        return self.get_policy()

    def _is_unrestricted_group(self, group_name: str) -> bool:
        with self._policy_lock:
            if not self._allowlist:
                return True
            return group_name in self._bypass_groups

    def _is_command_allowed(self, command_key: str, group_name: str) -> bool:
        with self._policy_lock:
            if not self._allowlist:
                return True
            if group_name in self._bypass_groups:
                return True
            return command_key.strip().lower() in self._allowlist

    def can_refresh_commands(self, group_name: str = "") -> bool:
        return self._is_unrestricted_group(group_name)

    def match_command(self, text: str, group_name: str = "") -> CommandMatch | None:
        text_lower = text.lower().strip()
        for key in sorted(self.command_keys, key=len, reverse=True):
            if text_lower.startswith(key.lower()):
                if not self._is_command_allowed(key, group_name):
                    return None
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

    def get_help_text(self, group_name: str = "") -> str:
        commands = [
            cmd for cmd in self.commands
            if self._is_command_allowed(cmd.get("key", ""), group_name)
        ]
        if not commands:
            if self.commands:
                return "当前群没有可用的远程命令"
            return "命令列表为空,请检查网络连接"
        lines = ["可用命令:"]
        for cmd in commands:
            lines.append(f"  {cmd['description']}")
        return "\n".join(lines)

    def close(self):
        self._client.close()
