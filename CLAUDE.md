# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AWSL WeChat Bot - 跨平台微信群聊机器人，使用 macOS Accessibility API / Windows UI Automation 读取消息并响应。支持 AI 智能回答、动态命令执行、定时任务和群聊总结。

## Development Commands

```bash
# Setup
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt  # macOS
./venv/Scripts/python -m pip install -r requirements.txt  # Windows
cp .env.example .env

# Run
./venv/bin/python main.py  # macOS
./venv/Scripts/python main.py  # Windows

# Daemon (Windows, 管理 bot 子进程)
./venv/Scripts/python daemon.py  # 启动 daemon (port 8001)

# Tools
./venv/bin/python tools/chat_summary.py --help  # 群聊总结工具
./venv/bin/python tools/wechat_decrypt.py       # 微信数据库解密
./venv/bin/python tools/inspect_wechat.py       # 微信窗口检查
```

## Architecture

```
┌─────────────────┐     ┌─────────────────┐
│  Detector (n)   │────▶│  Message Queue  │
│  per group      │     └────────┬────────┘
└─────────────────┘              │
                                 ▼
                    ┌─────────────────────────┐
                    │  TaskDispatcher         │
                    │  ThreadPoolExecutor(5)  │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │      Send Queue         │
                    └────────────┬────────────┘
                                 │
                                 ▼
                    ┌─────────────────────────┐
                    │  MessageSender          │
                    │  全局限速 10s            │
                    └─────────────────────────┘
```

### Core Components

- `src/bot.py` - 主机器人类，组件编排
- `src/core/detector.py` - 消息检测器，每群一个线程
- `src/core/dispatcher.py` - 任务分发器，ThreadPoolExecutor 并发处理
- `src/core/sender.py` - 消息发送器，全局限速
- `src/core/trigger.py` - 触发词匹配逻辑（支持 @BOT_NAMES 和关键词触发）
- `src/models/tasks.py` - 数据模型 (SendTask, MessageTask, TriggerType)
- `src/storage/message_db.py` - SQLite 消息去重，sha256 哈希

### Platform Adapters

`src/adapters/` - 平台适配器
- `base.py` - 抽象基类
- `macos.py` - macOS Accessibility API 实现
- `windows.py` - Windows UI Automation 实现

### Services

- `src/services/ai.py` - OpenAI API 调用
- `src/services/command.py` - 远程动态命令服务（httpx，300s 超时）
- `src/services/http/` - FastAPI HTTP 服务
  - `server.py` - 服务入口
  - `routes/` - API 路由 (groups, messages, tasks, chatlog, health)
  - `scheduler.py` - APScheduler 定时任务调度
  - `templates/index.html` - Web 管理后台

### Utilities

- `src/utils/wechat_chatlog.py` - 微信数据库解密和读取
- `src/utils/summary_service.py` - 群聊总结服务（异步任务）

## Key Configuration

配置文件: `.env` (基于 `.env.example`)

关键参数 (`config.py`):
- `TRIGGER_COOLDOWN: 3` - 群级冷却秒数
- `MAX_CONCURRENT_TASKS: 5` - 线程池大小
- `GLOBAL_SEND_INTERVAL: 10.0` - 全局发送间隔秒数
- `BOT_NAMES` - 机器人名称列表（@触发，逗号分隔）
- `HTTP_API_TOKEN` - Bearer Token 认证
- `HTTP_API_BASE_URL` - 内部回调地址
- `WECHAT_DB_*` - 微信数据库解密配置

## HTTP API

- API 文档: http://localhost:8000/docs
- Web 管理后台: http://localhost:8000
- 认证: `Authorization: Bearer <token>`

主要端点:
- `GET /api/groups` - 群组列表
- `POST /api/send` - 发送消息/图片
- `GET/POST/PUT/DELETE /api/tasks` - 定时任务 CRUD
- `POST /api/chatlog/decrypt` - 解密微信数据库
- `GET /api/chatlog/groups` - 群聊列表
- `GET /api/chatlog/messages` - 查询消息
- `POST /api/chatlog/summary` - 发送群聊总结

## Daemon 控制器

`daemon.py` - 独立 HTTP 服务 (port 8001)，管理 bot 子进程的启停和代码更新。由 Servy 注册为 Windows 服务。

配置: `DAEMON_PORT`, `DAEMON_HOST`, `DAEMON_RESTART_DELAY`, `DAEMON_MAX_RETRIES`, `DAEMON_LOG_MAX_LINES`

API 端点 (需要 Bearer Token 认证):
- `GET /status` - bot 运行状态、PID、uptime、重启次数
- `POST /start` - 启动 bot
- `POST /stop` - 停止 bot
- `POST /restart` - 重启 bot
- `POST /update` - git pull + pip install + 重启 (daemon.py 自身更新则 exit(0))
- `GET /logs?lines=100` - 读取 bot.log 最后 N 行

## Platform Notes

- **macOS**: 需要 Accessibility 权限 (系统设置 → 隐私与安全性 → 辅助功能)
- **Windows**: 建议管理员权限运行
