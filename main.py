#!/usr/bin/env python3
"""
AWSL 微信机器人 - 支持多平台、多群监听
监控多个群聊，检测到 "awsl" 消息时自动发送随机图片或AI回复
"""

import os
import sys
import time
import logging
import subprocess
import tempfile
import re
import sqlite3
import requests
import queue
import threading

from config import config
from src.adapters import get_wechat_adapter
from src.services.ai import AIService
from src.services.command import CommandService
from src.services.http import HTTPServer

# 根据配置设置日志级别
log_level = logging.DEBUG if config.DEBUG else logging.INFO

# 日志配置
logging.basicConfig(
    level=log_level,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class AWSlBot:
    """AWSL 机器人 - 支持多群监听"""

    def __init__(self):
        self.wechat = get_wechat_adapter()
        self.max_cache = 200

        # 群组配置（将在启动时初始化）
        self.groups = []  # [{"name": "群名", "window": WindowControl对象, "thread": Thread对象}]

        # 消息队列（最多30个待处理消息，因为有多个群）
        self.message_queue = queue.Queue(maxsize=30)

        # 群级别的冷却控制
        self.last_trigger_time = {}  # {group_name: timestamp}
        self.cooldown_lock = threading.Lock()

        # 数据库锁（保护数据库操作）
        self.db_lock = threading.Lock()

        # 运行控制
        self.running = False
        self.detector_threads = []  # 每个群一个检测线程
        self.processor_thread = None
        self.http_thread = None  # HTTP API 服务线程

        self._init_db()

        # 初始化 AI 服务
        try:
            self.ai_service = AIService()
        except Exception as e:
            logger.warning(f"AI 服务初始化失败，AI 功能将不可用: {e}")
            self.ai_service = None

        # 初始化命令服务
        try:
            self.command_service = CommandService()
            if self.command_service.load_commands():
                logger.info(f"命令服务初始化成功，已加载 {len(self.command_service.commands)} 个命令")
            else:
                logger.warning("命令列表加载失败")
        except Exception as e:
            logger.warning(f"命令服务初始化失败: {e}")
            self.command_service = None

        logger.info("AWSL Bot 初始化完成")

    def _init_db(self):
        """初始化 SQLite 数据库（支持群级别去重）"""
        db_path = os.path.join(os.path.dirname(__file__), 'messages.db')
        self.conn = sqlite3.connect(db_path, check_same_thread=False)

        # 检查是否需要迁移旧表结构
        cursor = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='message_hashes'"
        )
        if cursor.fetchone():
            # 检查是否已经有 group_name 字段
            cursor = self.conn.execute("PRAGMA table_info(message_hashes)")
            columns = [row[1] for row in cursor.fetchall()]
            if 'group_name' not in columns:
                logger.info("检测到旧数据库结构，正在迁移...")
                # 删除旧表，重新创建
                self.conn.execute("DROP TABLE message_hashes")
                self.conn.commit()

        # 创建新表结构（包含 group_name）
        self.conn.execute('''
            CREATE TABLE IF NOT EXISTS message_hashes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_name TEXT NOT NULL,
                hash TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(group_name, hash)
            )
        ''')
        self.conn.commit()
        logger.debug("数据库初始化完成")

    def _hash_message_with_context(self, messages: list, index: int, group_name: str) -> str:
        """结合前向上下文和群名计算消息的唯一哈希值"""
        current = messages[index]
        context_size = 2
        context_parts = []
        for i in range(max(0, index - context_size), index):
            context_parts.append(messages[i])
        context_parts.append(current)
        context = "|".join(context_parts)
        # 包含群名，避免不同群的相同消息被误判为重复
        return str(hash(f"{group_name}:{context}"))

    def _is_processed(self, msg_hash: str, group_name: str) -> bool:
        """检查消息是否已处理（群级别）"""
        with self.db_lock:
            cursor = self.conn.execute(
                'SELECT 1 FROM message_hashes WHERE hash = ? AND group_name = ?',
                (msg_hash, group_name)
            )
            return cursor.fetchone() is not None

    def _mark_processed(self, msg_hash: str, group_name: str):
        """标记消息为已处理（群级别）"""
        with self.db_lock:
            try:
                self.conn.execute(
                    'INSERT OR IGNORE INTO message_hashes (hash, group_name) VALUES (?, ?)',
                    (msg_hash, group_name)
                )
                self.conn.commit()
            except sqlite3.Error as e:
                logger.error(f"数据库写入失败: {e}")

    def _cleanup_old_hashes(self):
        """清理旧记录"""
        with self.db_lock:
            cursor = self.conn.execute('SELECT COUNT(*) FROM message_hashes')
            count = cursor.fetchone()[0]
            if count > self.max_cache:
                self.conn.execute('''
                    DELETE FROM message_hashes WHERE id IN (
                        SELECT id FROM message_hashes ORDER BY id ASC LIMIT ?
                    )
                ''', (count - self.max_cache // 2,))
                self.conn.commit()

    def fetch_awsl_image(self) -> str:
        """从 API 获取随机图片 URL"""
        try:
            response = requests.get(config.API_URL, timeout=10)
            response.raise_for_status()
            data = response.json()
            pic_info = data.get('pic_info', {})
            url = pic_info.get('large', pic_info.get('original', {})).get('url')
            return url
        except Exception as e:
            logger.error(f"获取图片失败: {e}")
            return None

    def download_image(self, url: str) -> str:
        """下载图片到临时文件"""
        try:
            response = requests.get(url, timeout=30)
            suffix = '.png' if 'png' in url.lower() else '.jpg'
            fd, temp_path = tempfile.mkstemp(suffix=suffix)
            with os.fdopen(fd, 'wb') as f:
                f.write(response.content)
            return temp_path
        except Exception as e:
            logger.error(f"下载图片失败: {e}")
            return None

    def is_trigger(self, text: str) -> tuple:
        """检查是否包含触发词"""
        if "animated stickers" in text.lower():
            return (None, "")
        content = text.strip()
        content_lower = content.lower()
        keyword_lower = config.TRIGGER_KEYWORD.lower()
        if content_lower == f"{keyword_lower} hp":
            return ("command_refresh", ("hp", ""))
        if content_lower.startswith(keyword_lower):
            after_keyword = content[len(config.TRIGGER_KEYWORD):].strip()
            if after_keyword:
                return ("ai", after_keyword)
            return (None, "")
        if self.command_service:
            cmd_match = self.command_service.match_command(content)
            if cmd_match:
                return ("command", cmd_match)
        return (None, "")

    def can_trigger(self, group_name: str) -> bool:
        """检查指定群是否在冷却期"""
        last_time = self.last_trigger_time.get(group_name, 0)
        return time.time() - last_time >= config.TRIGGER_COOLDOWN

    def mark_triggered(self, group_name: str):
        """标记指定群已触发"""
        self.last_trigger_time[group_name] = time.time()

    def message_detector_loop(self, group_name: str, window):
        """单个群的消息检测循环"""
        logger.info(f"[{group_name}] 消息检测线程启动")

        # 初始化：标记当前所有消息为已处理
        try:
            initial_messages = self.wechat.get_messages_from_window(window)
            for i, msg in enumerate(initial_messages):
                self._mark_processed(
                    self._hash_message_with_context(initial_messages, i, group_name),
                    group_name
                )
            logger.debug(f"[{group_name}] 已标记 {len(initial_messages)} 条初始消息")
        except Exception as e:
            logger.error(f"[{group_name}] 初始化失败: {e}")

        while self.running:
            try:
                # 检查窗口是否仍然存在
                if not window.Exists(0.5):
                    logger.warning(f"[{group_name}] 窗口已关闭，停止监听")
                    break

                # 获取消息
                messages = self.wechat.get_messages_from_window(window)

                # DEBUG: 打印完整消息列表
                if config.DEBUG:
                    logger.debug(f"[{group_name}] 消息列表({len(messages)}条): {[m[:20]+'...' if len(m)>20 else m for m in messages]}")

                messages_to_check = messages[-3:] if len(messages) > 3 else messages
                start_index = len(messages) - len(messages_to_check)

                # 检查新消息
                new_messages = []
                for i, msg in enumerate(messages_to_check):
                    msg_hash = self._hash_message_with_context(messages, start_index + i, group_name)
                    is_processed = self._is_processed(msg_hash, group_name)
                    idx = start_index + i
                    ctx_count = min(2, idx)  # 实际上下文数量
                    is_last = (i == len(messages_to_check) - 1)  # 是否是最后一条
                    if config.DEBUG:
                        ctx = [messages[j][:15]+'...' if len(messages[j])>15 else messages[j] for j in range(max(0, idx-2), idx)]
                        logger.debug(f"[{group_name}] [{i}] msg={msg[:30]}... ctx={ctx} hash={msg_hash[-8:]} processed={is_processed} is_last={is_last} ctx_count={ctx_count}")
                    if not is_processed:
                        # 只有最后一条消息（最新的）才触发
                        # 前面的消息即使未处理（哈希因滚动变化），也只标记不触发
                        if is_last:
                            new_messages.append((msg, msg_hash, True))  # True = 可触发
                        else:
                            new_messages.append((msg, msg_hash, False))  # False = 只标记不触发

                # 处理新消息
                for msg, msg_hash, can_trigger in new_messages:
                    self._mark_processed(msg_hash, group_name)
                    if not can_trigger:
                        if config.DEBUG:
                            logger.debug(f"[{group_name}] 上下文不足，跳过触发: {msg[:30]}...")
                        continue
                    trigger_type, content = self.is_trigger(msg)
                    if trigger_type:
                        logger.info(f"[{group_name}] 检测到触发: {msg}")
                        try:
                            self.message_queue.put_nowait({
                                'type': trigger_type,
                                'group_name': group_name,
                                'window': window,
                                'content': content,
                                'original_message': msg,
                                'timestamp': time.time()
                            })
                        except queue.Full:
                            logger.warning(f"[{group_name}] 队列已满，跳过消息")

                self._cleanup_old_hashes()
                time.sleep(config.CHECK_INTERVAL)
            except Exception as e:
                logger.error(f"[{group_name}] 检测出错: {e}")
                time.sleep(1)

        logger.info(f"[{group_name}] 消息检测线程退出")

    def message_processor_loop(self):
        """消息处理循环（串行发送）"""
        logger.info("消息处理线程启动")
        while self.running:
            try:
                task = self.message_queue.get(timeout=1)
                trigger_type = task['type']
                content = task['content']
                group_name = task['group_name']
                window = task['window']

                # 检查窗口是否仍然存在
                if not window.Exists(0.5):
                    logger.warning(f"[{group_name}] 目标窗口已关闭，跳过消息")
                    self.message_queue.task_done()
                    continue

                # 处理文本消息（定时任务或 HTTP API）
                if trigger_type == "text":
                    with self.cooldown_lock:
                        self.wechat.send_text_to_window(window, content)
                        self.mark_triggered(group_name)
                    self.message_queue.task_done()
                    continue

                # 处理图片消息（HTTP API 或定时任务）
                if trigger_type == "image":
                    with self.cooldown_lock:
                        self.wechat.send_image_to_window(window, content)
                        self.mark_triggered(group_name)
                    self.message_queue.task_done()
                    continue

                # 冷却控制（按群区分）
                with self.cooldown_lock:
                    if not self.can_trigger(group_name):
                        remaining = config.TRIGGER_COOLDOWN - (time.time() - self.last_trigger_time.get(group_name, 0))
                        logger.debug(f"[{group_name}] 冷却中，等待 {remaining:.1f} 秒")
                        time.sleep(remaining)

                    # 处理命令
                    if trigger_type == "command" and self.command_service:
                        logger.info(f"[{group_name}] 执行命令: {content[0]}")
                        res = self.command_service.execute_command(content[0], content[1])
                        if res:
                            if res.get("type") == "image":
                                # base64 图片
                                self.wechat.send_image_to_window(window, res["content"])
                            else:
                                self.wechat.send_text_to_window(window, res["content"])
                    # 刷新命令列表
                    elif trigger_type == "command_refresh":
                        logger.info(f"[{group_name}] 刷新命令列表")
                        if self.command_service.load_commands():
                            self.wechat.send_text_to_window(window, "已经成功了")
                    # AI 回复
                    elif trigger_type == "ai" and self.ai_service:
                        logger.info(f"[{group_name}] AI回复: {content}")
                        ans = self.ai_service.ask(content)
                        self.wechat.send_text_to_window(window, ans if ans else "抱歉，我现在无法回答这个问题 😅")

                    self.mark_triggered(group_name)

                self.message_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                logger.error(f"处理出错: {e}", exc_info=True)

    def select_groups_interactive(self) -> list[dict]:
        """自动选择所有打开的群聊窗口"""
        # 扫描所有微信窗口
        windows = self.wechat.find_all_wechat_windows()

        if not windows:
            logger.error("未找到任何群聊窗口！")
            logger.info("提示：请先打开微信，并双击弹出要监听的群聊窗口")
            return []

        # 显示所有窗口
        print(f"\n发现 {len(windows)} 个群聊窗口：")
        for i, w in enumerate(windows, 1):
            print(f"  [{i}] {w['title']}")

        # 自动监听全部
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
            self.groups.append({
                "name": w["title"],
                "window": w["window"],
                "thread": None
            })
            print(f"  - {w['title']}")
            # 初始化冷却时间
            self.last_trigger_time[w["title"]] = 0

        # 启动所有线程
        print("\n正在启动监听...")
        self.running = True

        # 为每个群创建检测线程
        for group in self.groups:
            thread = threading.Thread(
                target=self.message_detector_loop,
                args=(group["name"], group["window"]),
                daemon=True
            )
            thread.start()
            group["thread"] = thread
            logger.info(f"已启动检测线程: {group['name']}")

        # 启动处理线程
        self.processor_thread = threading.Thread(target=self.message_processor_loop, daemon=True)
        self.processor_thread.start()
        logger.info("已启动处理线程")

        # 启动 HTTP API 服务
        try:
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

        print(f"\n✓ 正在监听 {len(self.groups)} 个群...")
        print("按 Ctrl+C 停止监听\n")

        # 主循环
        try:
            while True:
                time.sleep(1)
                # 检查是否所有检测线程都已退出
                alive_threads = [g for g in self.groups if g["thread"].is_alive()]
                if not alive_threads:
                    logger.warning("所有检测线程已退出")
                    break
        except KeyboardInterrupt:
            logger.info("收到停止信号")
            self.running = False


def main():
    try:
        bot = AWSlBot()
        bot.run()
    except Exception as e:
        logger.error(f"启动失败: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()
