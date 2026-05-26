"""本地自定义命令服务 - 静态文本/图片/文件回复"""

import logging
import threading
from datetime import datetime

from pydantic import BaseModel
from sqlmodel import Field, Session, SQLModel, create_engine, select

from common.models import MessageType

logger = logging.getLogger(__name__)


def _now_local() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class CustomCommandBase(SQLModel):
    key: str
    description: str = ""
    message_type: MessageType = MessageType.TEXT
    message: str = ""
    filename: str = ""
    enabled: bool = True


class CustomCommand(CustomCommandBase, table=True):
    __tablename__ = "custom_commands"

    id: int | None = Field(default=None, primary_key=True)
    created_at: str | None = Field(default_factory=_now_local)
    updated_at: str | None = Field(default_factory=_now_local)


class CustomCommandCreate(CustomCommandBase):
    pass


class CustomCommandUpdate(SQLModel):
    key: str | None = None
    description: str | None = None
    message_type: MessageType | None = None
    message: str | None = None
    filename: str | None = None
    enabled: bool | None = None


class CustomCommandResponse(BaseModel):
    id: int
    key: str
    description: str
    message_type: MessageType
    message: str
    filename: str
    enabled: bool
    created_at: str | None = None
    updated_at: str | None = None


class CustomCommandService:
    """本地自定义命令服务,和远程 CommandService 平行存在"""

    def __init__(self, db_path: str):
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            echo=False,
        )
        CustomCommand.__table__.create(self.engine, checkfirst=True)
        self._cache_lock = threading.Lock()
        self._enabled_cache: list[CustomCommand] = []
        self._reload_cache()
        logger.info("自定义命令数据库初始化完成")

    @staticmethod
    def _normalize_key(key: str) -> str:
        return key.strip()

    @staticmethod
    def _normalize_content(resp_type: MessageType, content: str) -> str:
        if resp_type in {MessageType.IMAGE, MessageType.FILE} and content.startswith("data:"):
            return content.split(",", 1)[1] if "," in content else content
        return content

    @staticmethod
    def _validate_payload(*, key: str, message_type: MessageType, message: str, filename: str) -> bool:
        if not key.strip() or any(ch.isspace() for ch in key.strip()):
            return False
        if not message.strip():
            return False
        return not (message_type == MessageType.FILE and not filename.strip())

    def _reload_cache(self):
        with Session(self.engine) as session:
            commands = list(session.exec(
                select(CustomCommand)
                .where(CustomCommand.enabled == True)  # noqa: E712
                .order_by(CustomCommand.id)
            ).all())
        with self._cache_lock:
            self._enabled_cache = commands

    def _has_duplicate_key(self, key: str, exclude_id: int | None = None) -> bool:
        key_lower = key.lower()
        with Session(self.engine) as session:
            commands = session.exec(select(CustomCommand)).all()
            for command in commands:
                if exclude_id is not None and command.id == exclude_id:
                    continue
                if command.key.lower() == key_lower:
                    return True
        return False

    def create(self, data: CustomCommandCreate) -> CustomCommand | None:
        key = self._normalize_key(data.key)
        message = self._normalize_content(data.message_type, data.message)
        if not self._validate_payload(
            key=key,
            message_type=data.message_type,
            message=message,
            filename=data.filename,
        ):
            logger.error("创建自定义命令失败:命令内容不合法")
            return None
        if self._has_duplicate_key(key):
            logger.error(f"创建自定义命令失败:命令 key 已存在 '{key}'")
            return None

        with Session(self.engine) as session:
            command = CustomCommand.model_validate(data)
            command.key = key
            command.message = message
            session.add(command)
            session.commit()
            session.refresh(command)
            self._reload_cache()
            return command

    def get(self, command_id: int) -> CustomCommand | None:
        with Session(self.engine) as session:
            return session.get(CustomCommand, command_id)

    def get_all(self) -> list[CustomCommand]:
        with Session(self.engine) as session:
            return list(session.exec(select(CustomCommand).order_by(CustomCommand.id.desc())).all())

    def update(self, command_id: int, data: CustomCommandUpdate) -> CustomCommand | None:
        with Session(self.engine) as session:
            command = session.get(CustomCommand, command_id)
            if not command:
                return None

            update_data = data.model_dump(exclude_unset=True)
            next_key = self._normalize_key(update_data.get("key", command.key))
            next_message_type = update_data.get("message_type", command.message_type)
            next_message = update_data.get("message", command.message)
            next_message = self._normalize_content(next_message_type, next_message)
            next_filename = update_data.get("filename", command.filename)

            if not self._validate_payload(
                key=next_key,
                message_type=next_message_type,
                message=next_message,
                filename=next_filename,
            ):
                logger.error("更新自定义命令失败:命令内容不合法")
                return None
            if self._has_duplicate_key(next_key, exclude_id=command_id):
                logger.error(f"更新自定义命令失败:命令 key 已存在 '{next_key}'")
                return None

            update_data["key"] = next_key
            update_data["message"] = next_message
            command.sqlmodel_update(update_data)
            command.updated_at = _now_local()
            session.add(command)
            session.commit()
            session.refresh(command)
            self._reload_cache()
            return command

    def delete(self, command_id: int) -> bool:
        with Session(self.engine) as session:
            command = session.get(CustomCommand, command_id)
            if not command:
                return False
            session.delete(command)
            session.commit()
            self._reload_cache()
            return True

    def match_command(self, text: str) -> tuple[str, str] | None:
        text_lower = text.lower().strip()
        with self._cache_lock:
            commands = list(self._enabled_cache)
        for command in sorted(commands, key=lambda item: len(item.key), reverse=True):
            if text_lower.startswith(command.key.lower()):
                params = text[len(command.key):].strip()
                return (command.key, params)
        return None

    def execute_command(self, command_key: str, params: str = "") -> dict | None:
        key_lower = command_key.lower()
        with self._cache_lock:
            commands = list(self._enabled_cache)
        for command in commands:
            if command.key.lower() != key_lower:
                continue
            return {
                "content": command.message,
                "type": command.message_type.value,
                "filename": command.filename or None,
            }
        return None

    def close(self):
        self.engine.dispose()
