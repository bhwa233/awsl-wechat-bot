"""AWSL 微信机器人主类"""
import time
import queue
import logging
import threading
from typing import Any

from config import config
from src.adapters import get_wechat_adapter
from src.services.ai import AIService
from src.services.command import CommandService
from src.storage.message_db import MessageDatabase
from src.core.trigger import TriggerMatcher
from src.core.sender import MessageSender
from src.core.detector import MessageDetector
from src.core.dispatcher import TaskDispatcher
from src.models.tasks import SendTask

logger = logging.getLogger(__name__)


class AWSlBot:
    """AWSL 机器人 - 多群并发监听"""

    def __init__(self):
        # 适配器
        self.wechat = get_wechat_adapter()

        # 存储
        self.db = MessageDatabase()

        # 服务
        self.ai_service = self._init_ai_service()
        self.command_service = self._init_command_service()

        # 运行状态
        self.running = False
        self.group_windows: dict[str, Any] = {}  # {group_name: window}

        # 核心组件
        self.trigger_matcher = TriggerMatcher(self.command_service)
        self.message_queue: queue.Queue = queue.Queue(maxsize=30)
        self.sender = MessageSender(self.wechat, self.group_windows)
        self.dispatcher = TaskDispatcher(
            self.message_queue,
            self.sender,
            self.ai_service,
            self.command_service,
        )
        self._group_threads: dict[str, Any] = {}  # {group_name: thread} 内部使用
        self.http_thread = None
        self.webhook_service = None  # 由 HTTPServer 初始化后注入

        logger.info("AWSL Bot 初始化完成")

    def _init_ai_service(self):
        """初始化 AI 服务"""
        try:
            service = AIService()
            return service
        except Exception as e:
            logger.warning(f"AI 服务初始化失败，AI 功能将不可用: {e}")
            return None

    def _init_command_service(self):
        """初始化命令服务"""
        try:
            service = CommandService()
            if service.load_commands():
                logger.info(f"命令服务初始化成功，已加载 {len(service.commands)} 个命令")
            else:
                logger.warning("命令列表加载失败")
            return service
        except Exception as e:
            logger.warning(f"命令服务初始化失败: {e}")
            return None

    def send(self, task: SendTask):
        """发送消息（加入发送队列）"""
        self.sender.enqueue(task)

    def select_groups_interactive(self) -> list[dict]:
        """自动选择所有打开的群聊窗口"""
        windows = self.wechat.find_all_wechat_windows()

        if not windows:
            logger.error("未找到任何群聊窗口！")
            logger.info("提示：请先打开微信，并双击弹出要监听的群聊窗口")
            return []

        print(f"\n发现 {len(windows)} 个群聊窗口：")
        for i, w in enumerate(windows, 1):
            print(f"  [{i}] {w['title']}")

        logger.info("自动监听所有群聊窗口")
        return windows

    def run(self):
        """运行机器人"""
        # 选择要监听的群
        selected_windows = self.select_groups_interactive()

        if not selected_windows:
            logger.error("没有可监听的群聊，程序退出")
            return

        # 初始化群组配置
        print(f"\n已选择 {len(selected_windows)} 个群：")
        for w in selected_windows:
            group_name = w["title"]
            window = w["window"]
            self.group_windows[group_name] = window
            print(f"  - {group_name}")
            # 初始化冷却时间
            self.dispatcher.init_cooldown(group_name)

        # 启动所有组件
        print("\n正在启动监听...")
        self.running = True

        # 启动发送线程
        self.sender.start()

        # 启动分发线程
        self.dispatcher.start()

        # 启动 HTTP API 服务（需在创建检测线程之前，以便注入 webhook_service）
        self._start_http_server()

        # 为每个群创建检测线程
        for group_name, window in self.group_windows.items():
            detector = MessageDetector(
                group_name=group_name,
                window=window,
                wechat_adapter=self.wechat,
                message_queue=self.message_queue,
                db=self.db,
                trigger_matcher=self.trigger_matcher,
                webhook_service=self.webhook_service,
            )
            thread = threading.Thread(
                target=detector.run,
                args=(lambda: self.running,),
                daemon=True
            )
            thread.start()
            self._group_threads[group_name] = thread
            logger.info(f"已启动检测线程: {group_name}")

        print(f"\n正在监听 {len(self.group_windows)} 个群...")
        print("按 Ctrl+C 停止监听\n")

        # 主循环
        try:
            while self.running:
                time.sleep(1)
                alive_threads = [t for t in self._group_threads.values() if t.is_alive()]
                if not alive_threads:
                    logger.warning("所有检测线程已退出")
                    self._shutdown()
                    break
        except KeyboardInterrupt:
            logger.info("收到停止信号")
            self._shutdown()

    def _start_http_server(self):
        """启动 HTTP API 服务"""
        try:
            from src.services.http import HTTPServer
            http_server = HTTPServer(self)
            self.http_thread = threading.Thread(
                target=http_server.run,
                args=(config.HTTP_API_HOST, config.HTTP_API_PORT),
                daemon=True
            )
            self.http_thread.start()
            logger.info(f"已启动 HTTP API 服务: http://{config.HTTP_API_HOST}:{config.HTTP_API_PORT}")
            print(f"✓ HTTP API: http://{config.HTTP_API_HOST}:{config.HTTP_API_PORT}")
            print(f"  - GET  /api/groups   - 列出所有聊天窗口")
            print(f"  - POST /api/send     - 发送消息到指定窗口")
        except Exception as e:
            logger.error(f"启动 HTTP API 服务失败: {e}")

    def _shutdown(self):
        """关闭机器人"""
        self.running = False
        self.dispatcher.stop()
        self.sender.stop()
        if self.webhook_service:
            self.webhook_service.close()
        self.db.close()
