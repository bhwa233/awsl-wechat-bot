"""
定时任务调度器
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING

from .models import ChatSummaryRequest
from src.models.tasks import SendTask

if TYPE_CHECKING:
    from src.bot import WeChatBot
    from src.services.scheduled_task import ScheduledTaskService

logger = logging.getLogger(__name__)


class TaskScheduler:
    """定时任务调度器"""

    POLL_INTERVAL = 5  # 调度循环轮询间隔（秒）

    def __init__(self, task_service: ScheduledTaskService, bot_instance: WeChatBot):
        self.task_service = task_service
        self.bot = bot_instance
        self.running = False
        self.thread = None
        self.execution_lock = threading.Lock()
        self.executing_tasks = set()

    def _cleanup_executing_task(self, task_id: int):
        """从执行中任务集合移除"""
        with self.execution_lock:
            self.executing_tasks.discard(task_id)

    def start(self):
        """启动调度器"""
        self.running = True
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()
        logger.info("定时任务调度器已启动")

    def stop(self):
        """停止调度器"""
        self.running = False
        if self.thread:
            self.thread.join(timeout=5)
        logger.info("定时任务调度器已停止")

    def _loop(self):
        """调度循环"""
        logger.info("定时任务调度线程启动")

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

        logger.info("定时任务调度线程退出")

    def _execute_task(self, task):
        """执行任务"""
        with self.execution_lock:
            if task.id in self.executing_tasks:
                logger.debug(f"任务 {task.name} 正在执行中，跳过")
                return
            self.executing_tasks.add(task.id)

        try:
            logger.info(f"[Scheduler] 执行定时任务: {task.name} - {task.message_type}")
            self.task_service.update_last_run(task.id)

            # 处理 summary 类型任务（异步执行，由回调处理 task.id 移除）
            if task.message_type == "summary":
                self._execute_summary_task(task)
                return

            try:
                target_groups = json.loads(task.target_groups) if task.target_groups else []
            except json.JSONDecodeError:
                target_groups = []

            if not target_groups:
                groups_to_send = list(self.bot.group_windows.keys())
            else:
                groups_to_send = [name for name in target_groups if name in self.bot.group_windows]

            is_image = task.message_type == "image"
            content = task.image_base64 if is_image else task.message

            for group_name in groups_to_send:
                send_task = SendTask(
                    group_name=group_name,
                    content=content,
                    is_image=is_image,
                )
                self.bot.send(send_task)

        finally:
            # summary 类型任务由回调处理 task.id 移除，其他类型在此处移除
            if task.message_type != "summary":
                self._cleanup_executing_task(task.id)

    def _execute_summary_task(self, task):
        """执行群聊总结任务"""
        from config import config as app_config
        from src.utils.summary_service import start_chat_summary_async, SummaryConfig, SummaryGroup

        task_id = task.id  # 避免闭包直接捕获 task 对象

        try:
            # 解析 message 字段的 JSON 配置（只包含 groups）
            config_dict = json.loads(task.message)
            request = ChatSummaryRequest(**config_dict)

            # 从全局配置读取数据库相关参数
            if not app_config.wechat_db_configured:
                logger.error("[Scheduler] 总结任务失败: WECHAT_DB_* 环境变量未配置")
                self._cleanup_executing_task(task_id)
                return

            # 构建配置
            summary_config = SummaryConfig(
                input_path=app_config.WECHAT_DB_INPUT_PATH,
                key=app_config.WECHAT_DB_KEY,
                output_path=app_config.WECHAT_DB_OUTPUT_PATH,
                api_base=app_config.HTTP_API_BASE_URL,
                groups=[SummaryGroup(group_id=g.group_id, group_name=g.group_name) for g in request.groups],
                token=app_config.HTTP_API_TOKEN or None
            )

            # 完成回调
            def on_complete(result):
                level = logging.INFO if result.success else logging.ERROR
                status = "完成" if result.success else "失败"
                logger.log(level, f"[Scheduler] 群聊总结任务{status}: {result.message}")
                self._cleanup_executing_task(task_id)

            # 异步启动任务
            result = start_chat_summary_async(summary_config, on_complete)

            if result.success:
                logger.info(f"[Scheduler] 开始执行群聊总结任务: {[g.group_name for g in request.groups]}")
            else:
                logger.warning(f"[Scheduler] {result.message}")
                self._cleanup_executing_task(task_id)

        except json.JSONDecodeError as e:
            logger.error(f"[Scheduler] 总结任务配置解析失败: {e}")
            self._cleanup_executing_task(task_id)
        except Exception as e:
            logger.error(f"[Scheduler] 总结任务执行失败: {e}")
            self._cleanup_executing_task(task_id)
