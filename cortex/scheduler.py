"""定时任务调度器 - 通过 GatewayClient WS 发送消息"""

import json
import logging
import threading
import time
from datetime import datetime

import httpx

from common.models import OutgoingMessage, TaskType
from cortex.gateway_client import GatewayClient
from cortex.metrics import metrics
from cortex.services.scheduled_task import ScheduledTaskService
from cortex.services.summary_service import SummaryConfig, SummaryGroup, SummaryResult, SummaryService

logger = logging.getLogger(__name__)


class TaskScheduler:
    POLL_INTERVAL = 5

    def __init__(
        self,
        task_service: ScheduledTaskService,
        gateway_client: GatewayClient,
        summary_service: SummaryService,
        gateway_base_url: str = "",
        gateway_token: str = "",
    ):
        self.task_service = task_service
        self._gateway_client = gateway_client
        self._summary_service = summary_service
        self.gateway_base_url = gateway_base_url.rstrip("/")
        self.gateway_token = gateway_token
        self.running = False
        self.thread = None
        self.execution_lock = threading.Lock()
        self.executing_tasks: set[int] = set()
        self._client: httpx.Client | None = None
        if self.gateway_base_url:
            self._client = httpx.Client(
                base_url=self.gateway_base_url,
                headers={"Authorization": f"Bearer {gateway_token}"} if gateway_token else {},
                timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
            )

    def _cleanup_executing_task(self, task_id: int):
        with self.execution_lock:
            self.executing_tasks.discard(task_id)

    def start(self):
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True, name="TaskScheduler")
        self.thread.start()
        logger.info("定时任务调度器已启动")

    def stop(self):
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        if self._client:
            self._client.close()

    def _loop(self):
        while self.running:
            try:
                current_time = datetime.now()
                tasks = self.task_service.get_enabled_tasks()
                for task in tasks:
                    if self.task_service.should_run(task, current_time):
                        self._execute_task(task)
                time.sleep(self.POLL_INTERVAL)
            except Exception as e:
                logger.error(f"定时任务调度出错: {e}", exc_info=True)
                time.sleep(self.POLL_INTERVAL)

    def _execute_task(self, task):
        with self.execution_lock:
            if task.id in self.executing_tasks:
                return
            self.executing_tasks.add(task.id)
        metric_result = "success"
        try:
            logger.info(f"[Scheduler] 执行定时任务: {task.name} - {task.message_type}")
            self.task_service.update_last_run(task.id)

            if task.task_type == TaskType.SUMMARY:
                if not self._execute_summary_task(task):
                    metric_result = "failed"
                return

            try:
                target_groups = json.loads(task.target_groups) if task.target_groups else []
            except json.JSONDecodeError:
                target_groups = []

            if not target_groups:
                target_groups = self._get_active_groups()

            for group_name in target_groups:
                self._send_message(OutgoingMessage(
                    group_name=group_name,
                    message_type=task.message_type,
                    message=task.message,
                    filename=task.filename or None,
                ))
        except Exception:
            metric_result = "error"
            raise
        finally:
            metrics.increment("scheduler_tasks_total", task_type=task.task_type.value, result=metric_result)
            self._cleanup_executing_task(task.id)

    def _execute_summary_task(self, task):
        try:
            config_dict = json.loads(task.message)
            config = SummaryConfig(
                groups=[
                    SummaryGroup(group_id=item["group_id"], group_name=item["group_name"])
                    for item in config_dict.get("groups", [])
                    if item.get("group_id") and item.get("group_name")
                ],
                date=config_dict.get("date"),
            )
            if not config.groups:
                logger.warning("[Scheduler] 群聊总结启动失败: 未配置群聊")
                return False

            def on_complete(result: SummaryResult):
                if result.success:
                    logger.info(f"[Scheduler] 群聊总结完成: {result.message}")
                    return
                logger.warning(f"[Scheduler] 群聊总结失败: {result.message}")

            result = self._summary_service.start_summary_async(config, on_complete)
            if not result.success:
                logger.warning(f"[Scheduler] 群聊总结启动失败: {result.message}")
            return result.success
        except Exception as e:
            logger.error(f"[Scheduler] 总结任务执行失败: {e}")
            return False

    def _get_active_groups(self) -> list[str]:
        try:
            resp = self._gateway_get("/api/groups")
            if resp:
                return [g["name"] for g in resp if g.get("active")]
        except Exception as e:
            logger.error(f"[Scheduler] 获取群组列表失败: {e}")
        return []

    def _send_message(self, outgoing: OutgoingMessage):
        self._gateway_client.send_message(outgoing)

    def _gateway_get(self, path: str) -> list | dict | None:
        if not self._client:
            logger.warning(f"[Scheduler] Gateway HTTP 未配置,跳过 GET {path}")
            return None
        try:
            resp = self._client.get(path)
            return resp.json()
        except Exception as e:
            logger.error(f"[Scheduler] Gateway GET {path} 失败: {e}")
            return None
