# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AWSL WeChat Bot - 跨平台微信群聊机器人，采用 Gateway + Cortex 微服务架构。Gateway 负责微信窗口交互和 WS 消息广播，Cortex 作为 WS 客户端处理业务逻辑（AI/命令/触发/Webhook）。

## Development Commands

```bash
# Setup
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt  # macOS
./venv/Scripts/python -m pip install -r requirements.txt  # Windows
cp .env.example .env

# Run (Gateway :8000 + Cortex :7999)
./venv/bin/python main.py  # macOS
./venv/Scripts/python main.py  # Windows

# Run individually
./venv/bin/python -m gateway.main  # Gateway only (:8000)
./venv/bin/python -m cortex.main   # Cortex only (:7999)
./venv/bin/python run_daemon.py    # Daemon (:8001)

# Tools
./venv/bin/python tools/inspect_wechat.py   # 微信窗口检查
./venv/bin/python tools/wechat_decrypt.py   # 微信数据库解密
```

## Architecture

```
Detector (n) ──▶ gateway_queue ──▶ Gateway WS Server (:8000)
per group                              │ 广播
                          ┌────────────┼────────────┐
                          ▼            ▼            ▼
                    GatewayClient  WS Client A  WS Client B
                    (Cortex :7999) (外部程序)   (外部程序)
                          │
                    ┌─────┴─────┐
                    ▼           ▼
              CortexRuntime  WebhookService   ← MessageListener
              (AI/命令/触发)  (Webhook 转发)
                    │
                    └── send_message via WS ──▶ Gateway ──▶ Sender
```

### Services

| Service | Port | Role |
|---------|------|------|
| Gateway | 8000 | WS 消息网关 + REST API + 微信窗口交互 |
| Daemon  | 8001 | Bot 子进程管理（启停/更新） |
| Cortex  | 7999 | 业务逻辑（AI/命令/触发/Webhook/定时任务） |

### Key Components

**common/** - 共享模块
- `config.py` - Pydantic Settings 配置 (全局 `config` 实例，从 .env 读取)
- `models.py` - WS 协议 Pydantic 模型 (discriminated union 解析, WSType 常量, model_validator 校验)
- `auth.py` - Bearer Token 验证 (HMAC 常量时间比较)
- `logging.py` - HealthFilter + setup_uvicorn_logging()

**gateway/** - Gateway 服务
- `runtime.py` - GatewayRuntime：自动窗口发现（5s 扫描），管理 detectors/sender/gateway
- `asgi.py` - FastAPI app 工厂，lifespan 管理生命周期
- `core/gateway.py` - Gateway WS hub：队列消费 + 正则匹配广播
- `core/detector.py` - MessageDetector：per-group 消息检测线程，SHA256 上下文哈希去重
- `core/sender.py` - MessageSender：全局限速串行发送 (GLOBAL_SEND_INTERVAL)
- `adapters/base.py` - BaseWeChatAdapter 抽象基类 (4 个方法)
- `adapters/macos.py` - macOS: Quartz CGWindowListCreateImage 截图 + Vision OCR（无需激活窗口）
- `adapters/windows.py` - Windows: uiautomation 控件遍历
- `storage/message_db.py` - SQLModel ORM 消息去重 (SQLite)
- `routes/` - REST (health, groups, messages, chatlog) + WS 端点

**cortex/** - Cortex 服务
- `models.py` - TriggerType 枚举 + MessageListener 抽象基类（模板方法，异常由基类捕获）
- `runtime.py` - CortexRuntime (MessageListener)：滑动窗口限速/触发匹配(同步) + ThreadPoolExecutor 处理 AI/命令(异步)
- `asgi.py` - FastAPI app 工厂，组装 listener 注册 + scheduler + lifespan
- `gateway_client.py` - GatewayClient：纯 WS 客户端，连接/重连/订阅/收发，add_listener 注册 MessageListener
- `scheduler.py` - TaskScheduler：轮询定时任务，通过 GatewayClient WS 发消息，HTTP 仅用于 groups/summary 查询
- `services/ai.py` - httpx.Client 连接池调用 OpenAI API
- `services/command.py` - httpx.Client 调用命令 API
- `services/trigger.py` - TriggerMatcher 正则匹配（AI/命令/文本/图片触发）
- `services/webhook.py` - WebhookService (MessageListener)：SQLModel ORM + ThreadPoolExecutor fire-and-forget
- `services/scheduled_task.py` - ScheduledTaskService：SQLModel ORM CRUD + cron 调度
- `routes/` - tasks, webhooks, health

**daemon/** - Daemon 控制器
- `manager.py` - BotProcessManager 进程管理（健康检查 + 自动重启）
- `routes.py` - FastAPI 路由 (status/start/stop/restart/update/logs)

## Key Patterns

- **MessageListener 模式**: 抽象基类 `on_message(msg: WSMessageBroadcast)` + `_handle_message()`，异常由基类统一捕获。GatewayClient 通过 `add_listener()` 注册多个 listener（CortexRuntime、WebhookService）
- **非阻塞 listener**: listener 回调在 GatewayClient async 线程中执行，禁止阻塞调用。CortexRuntime 用 `ThreadPoolExecutor` 处理 AI/命令，WebhookService 用 `ThreadPoolExecutor` 发 HTTP
- **纯 WS 客户端**: GatewayClient 只管连接/重连/订阅/收发，不持有业务逻辑。指数退避重连 (1s → 30s)，asyncio.Event 优雅停止
- **Gateway/Cortex 通信**: 所有消息收发走 WS 协议。Scheduler 发消息也通过 GatewayClient WS，不走 HTTP self-call
- **macOS 截图**: Quartz `CGWindowListCreateImage` 按窗口 ID 内存截图，无需激活窗口，多群并行 OCR
- **macOS 发送**: 需要激活窗口（AppleScript AXRaise + 剪贴板粘贴），`_send_lock` 串行化
- **消息去重**: SHA256(前2条消息 + 当前消息 + 群名)，只触发窗口中最后一条消息
- **限速**: 全局发送间隔 `GLOBAL_SEND_INTERVAL`（默认 10s），群级触发限速 `TRIGGER_RATE_LIMIT`（默认 10次/60s 滑动窗口）
- **WS 协议模型**: Pydantic discriminated union + TypeAdapter 一步解析，WSType 常量，WSSendMessage model_validator 校验
- **HTTP 客户端**: 全局使用 httpx（无 requests 依赖），服务内持久连接池

## HTTP API

Gateway (:8000): `GET /api/health`, `GET /api/groups`, `POST /api/send`, `WS /ws?token=<token>`, chatlog 系列

Cortex (:7999): `GET /api/health`, CRUD `/api/tasks`, CRUD `/api/webhooks`

Daemon (:8001): `GET /status`, `POST /start|stop|restart|update`, `GET /logs`

认证: `Authorization: Bearer <HTTP_API_TOKEN>`，WS 认证: `ws://host:8000/ws?token=<token>`

## WS Protocol

连接: `ws://host:8000/ws?token=<bearer_token>`

Client → Server:
- `{"type": "subscribe", "group_filter": "regex", "message_filter": "regex"}`
- `{"type": "unsubscribe"}`
- `{"type": "send_message", "group_name": "...", "message": "...", "image_base64": "..."}`

Server → Client:
- `{"type": "subscribe_ok", ...}`
- `{"type": "message", "group_name": "...", "content": "...", "timestamp": ...}`
- `{"type": "send_ok"}`
- `{"type": "error", "detail": "..."}`

## Threading Model

- **Gateway 线程**: scan_loop(窗口发现) + detector(per-group OCR) + sender(串行发送) + consume_loop(队列广播)，各自独立线程
- **GatewayClient 线程**: 独立 asyncio 事件循环，只做 WS 收发 + listener 分发（listener 回调必须非阻塞）
- **Cortex ThreadPoolExecutor(4)**: AI/命令等阻塞操作从 listener 回调提交到此线程池
- **Webhook ThreadPoolExecutor(3)**: HTTP POST fire-and-forget
- **Scheduler 线程**: 5s 轮询定时任务，阻塞操作在此线程内（不影响消息处理）

## Platform Notes

- **macOS**: 需要辅助功能权限（发送）+ 屏幕录制权限（截图）
- **Windows**: 建议管理员权限运行
