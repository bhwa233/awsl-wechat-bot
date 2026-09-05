"""
定时任务服务
支持基于 cron 表达式的定时任务管理
"""

import logging
import threading
import time
from datetime import UTC, datetime

from croniter import croniter
from pydantic import BaseModel
from sqlmodel import Field, Session, SQLModel, create_engine, select

from common.models import MessageType, TaskType, binary_payload_too_large
from common.sqlite_migrator import SQLiteMigrator

logger = logging.getLogger(__name__)


def _now_local() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _now_utc() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")


# ==================== Models ====================

class ScheduledTaskBase(SQLModel):
    name: str
    cron_expression: str
    task_type: TaskType = TaskType.SEND
    message: str = ""
    message_type: MessageType = MessageType.TEXT
    filename: str = ""
    target_groups: str = ""
    enabled: bool = True


class ScheduledTask(ScheduledTaskBase, table=True):
    __tablename__ = "scheduled_tasks"
    id: int | None = Field(default=None, primary_key=True)
    created_at: str | None = Field(default_factory=_now_local)
    updated_at: str | None = Field(default_factory=_now_local)
    last_run: str | None = None


class ScheduledTaskCreate(ScheduledTaskBase):
    """service 层创建模型,target_groups 为 JSON 字符串"""
    pass


class ScheduledTaskUpdate(SQLModel):
    """service 层更新模型,target_groups 为 JSON 字符串"""
    name: str | None = None
    cron_expression: str | None = None
    task_type: TaskType | None = None
    message: str | None = None
    message_type: MessageType | None = None
    filename: str | None = None
    target_groups: str | None = None
    enabled: bool | None = None


# ==================== API Models ====================
# 前端交互用,target_groups 为 List[str]

class ScheduledTaskApiCreate(BaseModel):
    name: str
    cron_expression: str
    enabled: bool = True
    task_type: TaskType = TaskType.SEND
    message: str | None = ""
    message_type: MessageType = MessageType.TEXT
    filename: str | None = ""
    target_groups: list[str] = []


class ScheduledTaskApiUpdate(BaseModel):
    name: str | None = None
    cron_expression: str | None = None
    task_type: TaskType | None = None
    message: str | None = None
    message_type: MessageType | None = None
    filename: str | None = None
    target_groups: list[str] | None = None
    enabled: bool | None = None


class ScheduledTaskResponse(BaseModel):
    id: int
    name: str
    cron_expression: str
    task_type: TaskType
    message: str
    message_type: MessageType
    filename: str
    target_groups: list[str] = []
    enabled: bool
    created_at: str | None = None
    updated_at: str | None = None
    last_run: str | None = None


# ==================== Service ====================

class ScheduledTaskService:
    """定时任务服务"""

    _CACHE_TTL_SECONDS = 120
    _EXECUTION_WINDOW_SECONDS = 30

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            echo=False,
        )
        ScheduledTask.__table__.create(self.engine, checkfirst=True)
        self._run_migrations()
        self._run_cache_lock = threading.Lock()
        self._last_scheduled_run: dict[int, tuple[datetime, float]] = {}
        logger.info("定时任务数据库初始化完成")

    # ==================== CRUD ====================

    _MAX_CRON_LEN = 256

    def validate_cron_expression(self, cron_expr: str) -> bool:
        if len(cron_expr) > self._MAX_CRON_LEN:
            return False
        try:
            croniter(cron_expr)
            return True
        except Exception as e:
            logger.debug(f"无效的 cron 表达式 '{cron_expr}': {e}")
            return False

    def _run_migrations(self):
        migrator = SQLiteMigrator(self.db_path)

        def add_task_type(conn):
            SQLiteMigrator.add_column_if_missing(
                conn,
                "scheduled_tasks",
                f"task_type TEXT NOT NULL DEFAULT '{TaskType.SEND.name}'",
                "task_type",
            )

        def add_message_type(conn):
            SQLiteMigrator.add_column_if_missing(
                conn,
                "scheduled_tasks",
                f"message_type TEXT NOT NULL DEFAULT '{MessageType.TEXT.name}'",
                "message_type",
            )

        def add_filename(conn):
            SQLiteMigrator.add_column_if_missing(
                conn,
                "scheduled_tasks",
                "filename TEXT NOT NULL DEFAULT ''",
                "filename",
            )

        def migrate_summary_task_type(conn):
            columns = SQLiteMigrator.column_names(conn, "scheduled_tasks")
            if "message_type" not in columns or "task_type" not in columns:
                return
            conn.execute(
                """
                UPDATE scheduled_tasks
                SET task_type = 'SUMMARY', message_type = 'TEXT'
                WHERE message_type IN ('summary', 'SUMMARY')
                """
            )

        def migrate_image_payload(conn):
            columns = SQLiteMigrator.column_names(conn, "scheduled_tasks")
            if "image_base64" not in columns or "message_type" not in columns:
                return
            conn.execute(
                """
                UPDATE scheduled_tasks
                SET message = image_base64, message_type = 'IMAGE'
                WHERE message_type IN ('image', 'IMAGE')
                  AND COALESCE(image_base64, '') != ''
                """
            )

        def migrate_file_payload(conn):
            columns = SQLiteMigrator.column_names(conn, "scheduled_tasks")
            if "is_file" not in columns or "message_type" not in columns:
                return
            conn.execute(
                """
                UPDATE scheduled_tasks
                SET message_type = 'FILE'
                WHERE is_file = 1
                """
            )

        def backfill_defaults(conn):
            columns = SQLiteMigrator.column_names(conn, "scheduled_tasks")
            if "task_type" in columns:
                conn.execute(
                    """
                    UPDATE scheduled_tasks
                    SET task_type = 'SEND'
                    WHERE COALESCE(task_type, '') = ''
                    """
                )
            if "message_type" in columns:
                conn.execute(
                    """
                    UPDATE scheduled_tasks
                    SET message_type = 'TEXT'
                    WHERE COALESCE(message_type, '') = ''
                    """
                )

        def normalize_enum_storage(conn):
            columns = SQLiteMigrator.column_names(conn, "scheduled_tasks")
            if "task_type" in columns:
                conn.execute(
                    """
                    UPDATE scheduled_tasks
                    SET task_type = CASE task_type
                        WHEN 'send' THEN 'SEND'
                        WHEN 'summary' THEN 'SUMMARY'
                        ELSE task_type
                    END
                    """
                )
            if "message_type" in columns:
                conn.execute(
                    """
                    UPDATE scheduled_tasks
                    SET message_type = CASE message_type
                        WHEN 'text' THEN 'TEXT'
                        WHEN 'image' THEN 'IMAGE'
                        WHEN 'file' THEN 'FILE'
                        WHEN 'voice' THEN 'VOICE'
                        WHEN 'voice_file' THEN 'VOICE_FILE'
                        WHEN 'summary' THEN 'TEXT'
                        ELSE message_type
                    END
                    """
                )

        def normalize_voice_message_types(conn):
            columns = SQLiteMigrator.column_names(conn, "scheduled_tasks")
            if "message_type" not in columns:
                return
            conn.execute(
                """
                UPDATE scheduled_tasks
                SET message_type = CASE message_type
                    WHEN 'voice' THEN 'VOICE'
                    WHEN 'voice_file' THEN 'VOICE_FILE'
                    ELSE message_type
                END
                """
            )

        def repair_legacy_image_payload(conn):
            columns = SQLiteMigrator.column_names(conn, "scheduled_tasks")
            if "image_base64" not in columns or "message_type" not in columns:
                return
            conn.execute(
                """
                UPDATE scheduled_tasks
                SET message = image_base64, message_type = 'IMAGE'
                WHERE message_type IN ('image', 'IMAGE')
                  AND COALESCE(image_base64, '') != ''
                """
            )

        migrator.migrate([
            ("20260331_001_scheduled_tasks_add_task_type", add_task_type),
            ("20260331_002_scheduled_tasks_add_message_type", add_message_type),
            ("20260331_003_scheduled_tasks_add_filename", add_filename),
            ("20260331_004_scheduled_tasks_migrate_summary_task_type", migrate_summary_task_type),
            ("20260331_005_scheduled_tasks_migrate_image_payload", migrate_image_payload),
            ("20260331_006_scheduled_tasks_migrate_file_payload", migrate_file_payload),
            ("20260331_007_scheduled_tasks_backfill_defaults", backfill_defaults),
            ("20260331_008_scheduled_tasks_normalize_enum_storage", normalize_enum_storage),
            ("20260331_009_scheduled_tasks_repair_legacy_image_payload", repair_legacy_image_payload),
            ("20260809_001_scheduled_tasks_normalize_voice_message_types", normalize_voice_message_types),
        ])

    @staticmethod
    def _validate_task_payload(
        *,
        task_type: TaskType,
        message_type: MessageType,
        message: str,
        filename: str,
    ) -> bool:
        if task_type == TaskType.SUMMARY:
            return bool(message.strip())
        if not message.strip():
            return False
        if (
            message_type in {MessageType.IMAGE, MessageType.FILE, MessageType.VOICE_FILE}
            and binary_payload_too_large(message)
        ):
            return False
        return not (
            message_type in {MessageType.FILE, MessageType.VOICE_FILE}
            and not filename.strip()
        )

    def create_task(self, data: ScheduledTaskCreate) -> ScheduledTask | None:
        if not self.validate_cron_expression(data.cron_expression):
            logger.error(f"创建任务失败:无效的 cron 表达式 '{data.cron_expression}'")
            return None
        if not self._validate_task_payload(
            task_type=data.task_type,
            message_type=data.message_type,
            message=data.message,
            filename=data.filename,
        ):
            logger.error("创建任务失败:任务内容不合法")
            return None
        with Session(self.engine) as session:
            task = ScheduledTask.model_validate(data)
            session.add(task)
            session.commit()
            session.refresh(task)
            return task

    def get_task(self, task_id: int) -> ScheduledTask | None:
        with Session(self.engine) as session:
            return session.get(ScheduledTask, task_id)

    def get_all_tasks(self) -> list[ScheduledTask]:
        with Session(self.engine) as session:
            return list(session.exec(select(ScheduledTask).order_by(ScheduledTask.id.desc())).all())

    def get_enabled_tasks(self) -> list[ScheduledTask]:
        with Session(self.engine) as session:
            return list(session.exec(
                select(ScheduledTask).where(ScheduledTask.enabled == True).order_by(ScheduledTask.id)  # noqa: E712
            ).all())

    def update_task(self, task_id: int, data: ScheduledTaskUpdate) -> ScheduledTask | None:
        if data.cron_expression is not None and not self.validate_cron_expression(data.cron_expression):
            logger.error(f"更新任务失败:无效的 cron 表达式 '{data.cron_expression}'")
            return None
        with Session(self.engine) as session:
            task = session.get(ScheduledTask, task_id)
            if not task:
                return None
            update_data = data.model_dump(exclude_unset=True)
            next_task_type = update_data.get("task_type", task.task_type)
            next_message_type = update_data.get("message_type", task.message_type)
            next_message = update_data.get("message", task.message)
            next_filename = update_data.get("filename", task.filename)
            if not self._validate_task_payload(
                task_type=next_task_type,
                message_type=next_message_type,
                message=next_message,
                filename=next_filename,
            ):
                logger.error("更新任务失败:任务内容不合法")
                return None
            task.sqlmodel_update(update_data)
            task.updated_at = _now_local()
            session.add(task)
            session.commit()
            session.refresh(task)
            if data.cron_expression is not None or data.enabled is not None:
                self._clear_cached_run(task_id)
            return task

    def delete_task(self, task_id: int) -> bool:
        with Session(self.engine) as session:
            task = session.get(ScheduledTask, task_id)
            if not task:
                return False
            session.delete(task)
            session.commit()
            self._clear_cached_run(task_id)
            return True

    # ==================== 调度逻辑 ====================

    def update_last_run(self, task_id: int):
        """更新任务的最后运行时间(UTC)"""
        with Session(self.engine) as session:
            task = session.get(ScheduledTask, task_id)
            if task:
                task.last_run = _now_utc()
                session.add(task)
                session.commit()

    def should_run(self, task: ScheduledTask, current_time: datetime) -> bool:
        if not task.enabled:
            return False

        try:
            cron = croniter(task.cron_expression, current_time)
            prev_run_local = cron.get_prev(datetime)

            time_since_prev = (current_time - prev_run_local).total_seconds()
            if not (0 <= time_since_prev <= self._EXECUTION_WINDOW_SECONDS):
                return False

            cached_prev = self._get_cached_run(task.id)
            if cached_prev is not None and cached_prev == prev_run_local:
                return False

            if task.last_run is None or not task.last_run.strip():
                self._set_cached_run(task.id, prev_run_local)
                return True

            last_run_dt = self._parse_last_run(task.last_run)
            if last_run_dt is None:
                self._set_cached_run(task.id, prev_run_local)
                return True

            local_tz = datetime.now().astimezone().tzinfo
            prev_run_utc = prev_run_local.replace(tzinfo=local_tz).astimezone(UTC).replace(tzinfo=None)

            if prev_run_utc > last_run_dt:
                self._set_cached_run(task.id, prev_run_local)
                return True

            return False

        except Exception as e:
            logger.error(f"检查任务 {task.id} 运行时间失败: {e}")
            return False

    def _parse_last_run(self, last_run_str: str) -> datetime | None:
        try:
            last_run_str = last_run_str.replace('Z', '+00:00')
            if ' ' in last_run_str and '+' not in last_run_str:
                return datetime.strptime(last_run_str, '%Y-%m-%d %H:%M:%S')
            else:
                dt = datetime.fromisoformat(last_run_str)
                if dt.tzinfo is not None:
                    dt = dt.astimezone(UTC).replace(tzinfo=None)
                return dt
        except (ValueError, AttributeError) as e:
            logger.warning(f"无法解析 last_run 时间 '{last_run_str}': {e}")
            return None

    # ==================== 内存缓存 ====================

    def _get_cached_run(self, task_id: int) -> datetime | None:
        with self._run_cache_lock:
            cached = self._last_scheduled_run.get(task_id)
            if not cached:
                return None
            prev_run_local, cached_at = cached
            if time.time() - cached_at > self._CACHE_TTL_SECONDS:
                self._last_scheduled_run.pop(task_id, None)
                return None
            return prev_run_local

    def _set_cached_run(self, task_id: int, prev_run_local: datetime):
        with self._run_cache_lock:
            self._last_scheduled_run[task_id] = (prev_run_local, time.time())

    def _clear_cached_run(self, task_id: int):
        with self._run_cache_lock:
            self._last_scheduled_run.pop(task_id, None)

    def close(self):
        self.engine.dispose()
