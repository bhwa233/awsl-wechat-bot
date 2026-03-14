"""
定时任务服务
支持基于 cron 表达式的定时任务管理
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Optional, Dict, List

from croniter import croniter
from pydantic import BaseModel
from sqlmodel import SQLModel, Field, Session, create_engine, select

logger = logging.getLogger(__name__)


def _now_local() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


# ==================== Models ====================

class ScheduledTaskBase(SQLModel):
    name: str
    cron_expression: str
    message: str = ""
    message_type: str = "text"
    image_base64: str = ""
    target_groups: str = ""
    enabled: bool = True


class ScheduledTask(ScheduledTaskBase, table=True):
    __tablename__ = "scheduled_tasks"
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: Optional[str] = Field(default_factory=_now_local)
    updated_at: Optional[str] = Field(default_factory=_now_local)
    last_run: Optional[str] = None


class ScheduledTaskCreate(ScheduledTaskBase):
    """service 层创建模型，target_groups 为 JSON 字符串"""
    pass


class ScheduledTaskUpdate(SQLModel):
    """service 层更新模型，target_groups 为 JSON 字符串"""
    name: Optional[str] = None
    cron_expression: Optional[str] = None
    message: Optional[str] = None
    message_type: Optional[str] = None
    image_base64: Optional[str] = None
    target_groups: Optional[str] = None
    enabled: Optional[bool] = None


# ==================== API Models ====================
# 前端交互用，target_groups 为 List[str]

class ScheduledTaskApiCreate(BaseModel):
    name: str
    cron_expression: str
    message: Optional[str] = ""
    message_type: str = "text"
    image_base64: Optional[str] = ""
    target_groups: List[str] = []


class ScheduledTaskApiUpdate(BaseModel):
    name: Optional[str] = None
    cron_expression: Optional[str] = None
    message: Optional[str] = None
    message_type: Optional[str] = None
    image_base64: Optional[str] = None
    target_groups: Optional[List[str]] = None
    enabled: Optional[bool] = None


class ScheduledTaskResponse(BaseModel):
    id: int
    name: str
    cron_expression: str
    message: str
    message_type: str
    image_base64: str
    target_groups: List[str] = []
    enabled: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    last_run: Optional[str] = None


# ==================== Service ====================

class ScheduledTaskService:
    """定时任务服务"""

    _CACHE_TTL_SECONDS = 120
    _EXECUTION_WINDOW_SECONDS = 30

    def __init__(self, db_path: str):
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            echo=False,
        )
        ScheduledTask.__table__.create(self.engine, checkfirst=True)
        self._run_cache_lock = threading.Lock()
        self._last_scheduled_run: Dict[int, tuple[datetime, float]] = {}
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

    def create_task(self, data: ScheduledTaskCreate) -> Optional[ScheduledTask]:
        if not self.validate_cron_expression(data.cron_expression):
            logger.error(f"创建任务失败：无效的 cron 表达式 '{data.cron_expression}'")
            return None
        with Session(self.engine) as session:
            task = ScheduledTask.model_validate(data)
            session.add(task)
            session.commit()
            session.refresh(task)
            return task

    def get_task(self, task_id: int) -> Optional[ScheduledTask]:
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

    def update_task(self, task_id: int, data: ScheduledTaskUpdate) -> Optional[ScheduledTask]:
        if data.cron_expression is not None and not self.validate_cron_expression(data.cron_expression):
            logger.error(f"更新任务失败：无效的 cron 表达式 '{data.cron_expression}'")
            return None
        with Session(self.engine) as session:
            task = session.get(ScheduledTask, task_id)
            if not task:
                return None
            update_data = data.model_dump(exclude_unset=True)
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
        """更新任务的最后运行时间（UTC）"""
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
            prev_run_utc = prev_run_local.replace(tzinfo=local_tz).astimezone(timezone.utc).replace(tzinfo=None)

            if prev_run_utc > last_run_dt:
                self._set_cached_run(task.id, prev_run_local)
                return True

            return False

        except Exception as e:
            logger.error(f"检查任务 {task.id} 运行时间失败: {e}")
            return False

    def _parse_last_run(self, last_run_str: str) -> Optional[datetime]:
        try:
            last_run_str = last_run_str.replace('Z', '+00:00')
            if ' ' in last_run_str and '+' not in last_run_str:
                return datetime.strptime(last_run_str, '%Y-%m-%d %H:%M:%S')
            else:
                dt = datetime.fromisoformat(last_run_str)
                if dt.tzinfo is not None:
                    dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                return dt
        except (ValueError, AttributeError) as e:
            logger.warning(f"无法解析 last_run 时间 '{last_run_str}': {e}")
            return None

    # ==================== 内存缓存 ====================

    def _get_cached_run(self, task_id: int) -> Optional[datetime]:
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
