"""本地自定义命令服务 - 静态文本/图片/文件回复"""

import json
import logging
import threading
from contextlib import suppress
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field as PydanticField
from sqlmodel import Field, Session, SQLModel, create_engine, select

from common.models import MessageType, OutgoingMessage
from common.sqlite_migrator import SQLiteMigrator
from cortex.models import CommandMatch
from cortex.services.summary_service import SummaryService

logger = logging.getLogger(__name__)


def _now_local() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class CustomCommandActionType(StrEnum):
    TEXT = "text"
    IMAGE = "image"
    FILE = "file"
    SUMMARY = "summary"

    def to_message_type(self) -> MessageType:
        if self == CustomCommandActionType.IMAGE:
            return MessageType.IMAGE
        if self == CustomCommandActionType.FILE:
            return MessageType.FILE
        return MessageType.TEXT


class CustomCommandBase(SQLModel):
    key: str
    description: str = ""
    action_type: CustomCommandActionType = CustomCommandActionType.TEXT
    message: str = ""
    filename: str = ""
    target_groups: str = ""
    enabled: bool = True


class CustomCommand(CustomCommandBase, table=True):
    __tablename__ = "custom_commands"

    id: int | None = Field(default=None, primary_key=True)
    created_at: str | None = Field(default_factory=_now_local)
    updated_at: str | None = Field(default_factory=_now_local)


class CustomCommandCreate(CustomCommandBase):
    target_groups: list[str] | str | None = None


class CustomCommandUpdate(SQLModel):
    key: str | None = None
    description: str | None = None
    action_type: CustomCommandActionType | None = None
    message: str | None = None
    filename: str | None = None
    target_groups: list[str] | str | None = None
    enabled: bool | None = None


class CustomCommandResponse(BaseModel):
    id: int
    key: str
    description: str
    action_type: CustomCommandActionType
    message: str
    filename: str
    target_groups: list[str] = PydanticField(default_factory=list)
    enabled: bool
    created_at: str | None = None
    updated_at: str | None = None


class CustomCommandService:
    """本地自定义命令服务,和远程 CommandService 平行存在"""

    def __init__(self, db_path: str, summary_service: SummaryService):
        self._summary_service = summary_service
        self._cache_lock = threading.Lock()
        self._enabled_cache: list[CustomCommand] = []
        self.engine = None

        engine = None
        try:
            engine = create_engine(
                f"sqlite:///{db_path}",
                connect_args={"check_same_thread": False},
                echo=False,
            )
            CustomCommand.__table__.create(engine, checkfirst=True)
            self._run_migrations(db_path)
            self.engine = engine
            self._reload_cache()
            logger.info("自定义命令数据库初始化完成")
        except Exception as e:
            logger.warning(f"自定义命令数据库初始化失败,将返回空结果: {e}", exc_info=True)
            if engine:
                with suppress(Exception):
                    engine.dispose()

    @staticmethod
    def _normalize_key(key: str) -> str:
        return key.strip()

    @staticmethod
    def _normalize_content(action_type: CustomCommandActionType, content: str) -> str:
        if action_type in {CustomCommandActionType.IMAGE, CustomCommandActionType.FILE} and content.startswith("data:"):
            return content.split(",", 1)[1] if "," in content else content
        return content

    @staticmethod
    def _normalize_target_groups(value: list[str] | str | None) -> str:
        if value is None:
            return "[]"
        if isinstance(value, str):
            if not value.strip():
                return "[]"
            try:
                groups = json.loads(value)
            except json.JSONDecodeError:
                return "[]"
        else:
            groups = value
        if not isinstance(groups, list):
            return "[]"
        normalized = [str(group).strip() for group in groups if str(group).strip()]
        return json.dumps(normalized, ensure_ascii=False)

    @staticmethod
    def _target_groups_to_list(value: str | None) -> list[str]:
        if not value:
            return []
        try:
            groups = json.loads(value)
        except json.JSONDecodeError:
            return []
        if not isinstance(groups, list):
            return []
        return [str(group) for group in groups if str(group)]

    @staticmethod
    def _validate_payload(
        *,
        key: str,
        action_type: CustomCommandActionType,
        message: str,
        filename: str,
    ) -> bool:
        if not key.strip() or any(ch.isspace() for ch in key.strip()):
            return False
        if action_type == CustomCommandActionType.SUMMARY:
            return True
        if not message.strip():
            return False
        return not (action_type == CustomCommandActionType.FILE and not filename.strip())

    def _run_migrations(self, db_path: str):
        migrator = SQLiteMigrator(db_path)

        def add_target_groups(conn):
            SQLiteMigrator.add_column_if_missing(
                conn,
                "custom_commands",
                "target_groups TEXT NOT NULL DEFAULT '[]'",
                "target_groups",
            )

        def add_action_type(conn):
            SQLiteMigrator.add_column_if_missing(
                conn,
                "custom_commands",
                f"action_type TEXT NOT NULL DEFAULT '{CustomCommandActionType.TEXT.name}'",
                "action_type",
            )

        def normalize_action_type(conn):
            columns = SQLiteMigrator.column_names(conn, "custom_commands")
            if "action_type" in columns:
                conn.execute(
                    """
                    UPDATE custom_commands
                    SET action_type = CASE action_type
                        WHEN 'TEXT' THEN 'TEXT'
                        WHEN 'IMAGE' THEN 'IMAGE'
                        WHEN 'FILE' THEN 'FILE'
                        WHEN 'SUMMARY' THEN 'SUMMARY'
                        WHEN 'reply' THEN 'TEXT'
                        WHEN 'REPLY' THEN 'TEXT'
                        WHEN 'text' THEN 'TEXT'
                        WHEN 'image' THEN 'IMAGE'
                        WHEN 'file' THEN 'FILE'
                        WHEN 'summary' THEN 'SUMMARY'
                        ELSE 'TEXT'
                    END
                    """
                )
            if "message_type" in columns:
                conn.execute(
                    """
                    UPDATE custom_commands
                    SET action_type = CASE message_type
                        WHEN 'text' THEN 'TEXT'
                        WHEN 'TEXT' THEN 'TEXT'
                        WHEN 'image' THEN 'IMAGE'
                        WHEN 'IMAGE' THEN 'IMAGE'
                        WHEN 'file' THEN 'FILE'
                        WHEN 'FILE' THEN 'FILE'
                        ELSE action_type
                    END
                    WHERE action_type IN ('', 'TEXT', 'REPLY', 'reply')
                    """
                )
                conn.execute(
                    """
                    UPDATE custom_commands
                    SET action_type = 'SUMMARY'
                    WHERE message_type IN ('summary', 'SUMMARY')
                    """
                )

        def drop_legacy_message_type(conn):
            columns = SQLiteMigrator.column_names(conn, "custom_commands")
            if "message_type" not in columns:
                return

            def col_expr(column: str, default: str) -> str:
                return column if column in columns else default

            action_expr = """
                CASE
                    WHEN action_type IN ('TEXT', 'IMAGE', 'FILE', 'SUMMARY') THEN action_type
                    WHEN action_type = 'text' THEN 'TEXT'
                    WHEN action_type = 'image' THEN 'IMAGE'
                    WHEN action_type = 'file' THEN 'FILE'
                    WHEN action_type = 'summary' THEN 'SUMMARY'
                    WHEN message_type IN ('TEXT', 'IMAGE', 'FILE') THEN message_type
                    WHEN message_type = 'text' THEN 'TEXT'
                    WHEN message_type = 'image' THEN 'IMAGE'
                    WHEN message_type = 'file' THEN 'FILE'
                    WHEN message_type IN ('summary', 'SUMMARY') THEN 'SUMMARY'
                    ELSE 'TEXT'
                END
            """
            conn.execute(
                """
                CREATE TABLE custom_commands_new (
                    id INTEGER PRIMARY KEY,
                    key VARCHAR NOT NULL,
                    description VARCHAR NOT NULL DEFAULT '',
                    action_type VARCHAR NOT NULL DEFAULT 'TEXT',
                    message VARCHAR NOT NULL DEFAULT '',
                    filename VARCHAR NOT NULL DEFAULT '',
                    target_groups TEXT NOT NULL DEFAULT '[]',
                    enabled BOOLEAN NOT NULL DEFAULT 1,
                    created_at VARCHAR,
                    updated_at VARCHAR
                )
                """
            )
            conn.execute(
                f"""
                INSERT INTO custom_commands_new (
                    id,
                    key,
                    description,
                    action_type,
                    message,
                    filename,
                    target_groups,
                    enabled,
                    created_at,
                    updated_at
                )
                SELECT
                    id,
                    key,
                    COALESCE({col_expr("description", "''")}, ''),
                    {action_expr},
                    COALESCE({col_expr("message", "''")}, ''),
                    COALESCE({col_expr("filename", "''")}, ''),
                    COALESCE({col_expr("target_groups", "'[]'")}, '[]'),
                    COALESCE({col_expr("enabled", "1")}, 1),
                    {col_expr("created_at", "NULL")},
                    {col_expr("updated_at", "NULL")}
                FROM custom_commands
                """
            )
            conn.execute("DROP TABLE custom_commands")
            conn.execute("ALTER TABLE custom_commands_new RENAME TO custom_commands")

        def repair_action_type(conn):
            columns = SQLiteMigrator.column_names(conn, "custom_commands")
            if "action_type" not in columns:
                return
            conn.execute(
                """
                UPDATE custom_commands
                SET action_type = CASE action_type
                    WHEN 'TEXT' THEN 'TEXT'
                    WHEN 'IMAGE' THEN 'IMAGE'
                    WHEN 'FILE' THEN 'FILE'
                    WHEN 'SUMMARY' THEN 'SUMMARY'
                    WHEN 'reply' THEN 'TEXT'
                    WHEN 'REPLY' THEN 'TEXT'
                    WHEN 'text' THEN 'TEXT'
                    WHEN 'image' THEN 'IMAGE'
                    WHEN 'file' THEN 'FILE'
                    WHEN 'summary' THEN 'SUMMARY'
                    ELSE 'TEXT'
                END
                """
            )

        migrator.migrate([
            ("20260526_001_custom_commands_add_target_groups", add_target_groups),
            ("20260529_001_custom_commands_add_action_type", add_action_type),
            ("20260529_002_custom_commands_normalize_action_type", normalize_action_type),
            ("20260529_003_custom_commands_drop_legacy_message_type", drop_legacy_message_type),
            ("20260529_004_custom_commands_repair_action_type", repair_action_type),
        ])

    def to_response(self, command: CustomCommand) -> CustomCommandResponse:
        return CustomCommandResponse(
            id=command.id,
            key=command.key,
            description=command.description,
            action_type=command.action_type,
            message=command.message,
            filename=command.filename,
            target_groups=self._target_groups_to_list(command.target_groups),
            enabled=command.enabled,
            created_at=command.created_at,
            updated_at=command.updated_at,
        )

    def _reload_cache(self):
        if not self.engine:
            with self._cache_lock:
                self._enabled_cache = []
            return
        try:
            with Session(self.engine) as session:
                commands = list(session.exec(
                    select(CustomCommand)
                    .where(CustomCommand.enabled == True)  # noqa: E712
                    .order_by(CustomCommand.id)
                ).all())
        except Exception as e:
            logger.warning(f"刷新自定义命令缓存失败: {e}", exc_info=True)
            commands = []
        with self._cache_lock:
            self._enabled_cache = commands

    def _has_duplicate_key(self, key: str, exclude_id: int | None = None) -> bool:
        if not self.engine:
            return False
        key_lower = key.lower()
        try:
            with Session(self.engine) as session:
                commands = session.exec(select(CustomCommand)).all()
                for command in commands:
                    if exclude_id is not None and command.id == exclude_id:
                        continue
                    if command.key.lower() == key_lower:
                        return True
        except Exception as e:
            logger.warning(f"检查自定义命令重复 key 失败: {e}", exc_info=True)
            return True
        return False

    def create(self, data: CustomCommandCreate) -> CustomCommand | None:
        if not self.engine:
            logger.warning("创建自定义命令失败:自定义命令数据库不可用")
            return None
        key = self._normalize_key(data.key)
        message = self._normalize_content(data.action_type, data.message)
        target_groups = self._normalize_target_groups(data.target_groups)
        if not self._validate_payload(
            key=key,
            action_type=data.action_type,
            message=message,
            filename=data.filename,
        ):
            logger.error("创建自定义命令失败:命令内容不合法")
            return None
        if self._has_duplicate_key(key):
            logger.error(f"创建自定义命令失败:命令 key 已存在 '{key}'")
            return None

        try:
            with Session(self.engine) as session:
                command = CustomCommand.model_validate(data.model_dump(exclude={"target_groups"}))
                command.key = key
                command.message = message
                command.target_groups = target_groups
                session.add(command)
                session.commit()
                session.refresh(command)
                self._reload_cache()
                return command
        except Exception as e:
            logger.warning(f"创建自定义命令失败: {e}", exc_info=True)
            return None

    def get(self, command_id: int) -> CustomCommand | None:
        if not self.engine:
            return None
        try:
            with Session(self.engine) as session:
                return session.get(CustomCommand, command_id)
        except Exception as e:
            logger.warning(f"获取自定义命令失败: {e}", exc_info=True)
            return None

    def get_all(self) -> list[CustomCommand]:
        if not self.engine:
            return []
        try:
            with Session(self.engine) as session:
                return list(session.exec(select(CustomCommand).order_by(CustomCommand.id.desc())).all())
        except Exception as e:
            logger.warning(f"获取自定义命令列表失败: {e}", exc_info=True)
            return []

    def update(self, command_id: int, data: CustomCommandUpdate) -> CustomCommand | None:
        if not self.engine:
            logger.warning("更新自定义命令失败:自定义命令数据库不可用")
            return None
        try:
            with Session(self.engine) as session:
                command = session.get(CustomCommand, command_id)
                if not command:
                    return None

                update_data = data.model_dump(exclude_unset=True)
                next_key = self._normalize_key(update_data.get("key", command.key))
                next_action_type = update_data.get("action_type", command.action_type)
                next_message = update_data.get("message", command.message)
                next_message = self._normalize_content(next_action_type, next_message)
                next_filename = update_data.get("filename", command.filename)
                next_target_groups = self._normalize_target_groups(update_data.get("target_groups", command.target_groups))

                if not self._validate_payload(
                    key=next_key,
                    action_type=next_action_type,
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
                update_data["target_groups"] = next_target_groups
                command.sqlmodel_update(update_data)
                command.updated_at = _now_local()
                session.add(command)
                session.commit()
                session.refresh(command)
                self._reload_cache()
                return command
        except Exception as e:
            logger.warning(f"更新自定义命令失败: {e}", exc_info=True)
            return None

    def delete(self, command_id: int) -> bool:
        if not self.engine:
            logger.warning("删除自定义命令失败:自定义命令数据库不可用")
            return False
        try:
            with Session(self.engine) as session:
                command = session.get(CustomCommand, command_id)
                if not command:
                    return False
                session.delete(command)
                session.commit()
                self._reload_cache()
                return True
        except Exception as e:
            logger.warning(f"删除自定义命令失败: {e}", exc_info=True)
            return False

    @staticmethod
    def _matches_group(command: CustomCommand, group_name: str) -> bool:
        target_groups = CustomCommandService._target_groups_to_list(command.target_groups)
        return not target_groups or group_name in target_groups

    def match_command(self, text: str, group_name: str = "") -> CommandMatch | None:
        text_lower = text.lower().strip()
        with self._cache_lock:
            commands = list(self._enabled_cache)
        for command in sorted(commands, key=lambda item: len(item.key), reverse=True):
            if not self._matches_group(command, group_name):
                continue
            if text_lower.startswith(command.key.lower()):
                params = text[len(command.key):].strip()
                return CommandMatch(key=command.key, params=params)
        return None

    @staticmethod
    def _text_message(group_name: str, content: str) -> OutgoingMessage:
        return OutgoingMessage(
            group_name=group_name,
            message_type=MessageType.TEXT,
            message=content,
        )

    def _execute_summary(self, group_name: str) -> OutgoingMessage | None:
        result = self._summary_service.start_summary_for_group_name(group_name, log_prefix="[CustomCommand]")
        if result.success:
            return None

        logger.warning(f"[CustomCommand] 总结命令启动失败: {result.message}")
        return self._text_message(group_name, result.message or "总结任务启动失败")

    def execute_command(
        self,
        command_key: str,
        params: str = "",
        group_name: str = "",
    ) -> OutgoingMessage | None:
        key_lower = command_key.lower()
        with self._cache_lock:
            commands = list(self._enabled_cache)
        for command in commands:
            if command.key.lower() != key_lower:
                continue
            if command.action_type == CustomCommandActionType.SUMMARY:
                return self._execute_summary(group_name)
            return OutgoingMessage(
                group_name=group_name,
                message_type=command.action_type.to_message_type(),
                message=command.message,
                filename=command.filename or None,
            )
        return None

    def close(self):
        if self.engine:
            self.engine.dispose()
