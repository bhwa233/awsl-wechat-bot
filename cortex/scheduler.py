"""定时任务调度器 - 通过 GatewayClient WS 发送消息"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from cortex.gateway_client import GatewayClient
    from cortex.services.scheduled_task import ScheduledTaskService

logger = logging.getLogger(__name__)


class TaskScheduler:
    POLL_INTERVAL = 5

    def __init__(
        self,
        task_service: ScheduledTaskService,
        gateway_client: GatewayClient,
        gateway_base_url: str = "",
        gateway_token: str = "",
    ):
        self.task_service = task_service
        self._gateway_client = gateway_client
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
        try:
            logger.info(f"[Scheduler] 执行定时任务: {task.name} - {task.message_type}")
            self.task_service.update_last_run(task.id)

            if task.message_type == "summary":
                self._execute_summary_task(task)
                return

            try:
                target_groups = json.loads(task.target_groups) if task.target_groups else []
            except json.JSONDecodeError:
                target_groups = []

            if not target_groups:
                target_groups = self._get_active_groups()

            is_image = task.message_type == "image"
            content = task.image_base64 if is_image else task.message

            for group_name in target_groups:
                self._send_message(group_name, content, is_image=is_image)
        finally:
            if task.message_type != "summary":
                self._cleanup_executing_task(task.id)

    def _execute_summary_task(self, task):
        task_id = task.id
        try:
            config_dict = json.loads(task.message)
            groups = config_dict.get("groups", [])
            resp = self._gateway_post("/api/chatlog/summary", {"groups": groups})
            if resp and resp.get("success"):
                logger.info("[Scheduler] 群聊总结任务已提交")
            else:
                msg = resp.get("message", "unknown") if resp else "no response"
                logger.warning(f"[Scheduler] 群聊总结提交失败: {msg}")
        except Exception as e:
            logger.error(f"[Scheduler] 总结任务执行失败: {e}")
        finally:
            self._cleanup_executing_task(task_id)

    def _get_active_groups(self) -> list[str]:
        try:
            resp = self._gateway_get("/api/groups")
            if resp:
                return [g["name"] for g in resp if g.get("active")]
        except Exception as e:
            logger.error(f"[Scheduler] 获取群组列表失败: {e}")
        return []

    def _send_message(self, group_name: str, content: str, *, is_image: bool = False):
        self._gateway_client.send_message(group_name, content, is_image=is_image)

    def _gateway_post(self, path: str, body: dict) -> dict | None:
        if not self._client:
            logger.warning(f"[Scheduler] Gateway HTTP 未配置，跳过 POST {path}")
            return None
        try:
            resp = self._client.post(path, json=body)
            return resp.json()
        except Exception as e:
            logger.error(f"[Scheduler] Gateway POST {path} 失败: {e}")
            return None

    def _gateway_get(self, path: str) -> list | dict | None:
        if not self._client:
            logger.warning(f"[Scheduler] Gateway HTTP 未配置，跳过 GET {path}")
            return None
        try:
            resp = self._client.get(path)
            return resp.json()
        except Exception as e:
            logger.error(f"[Scheduler] Gateway GET {path} 失败: {e}")
            return None
