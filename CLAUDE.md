# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AWSL WeChat Bot - 跨平台微信群聊机器人，采用 Gateway + Cortex 微服务架构。Gateway 负责微信窗口交互和 WS 消息广播，Cortex 作为 WS 客户端处理业务逻辑（AI/命令/触发/Webhook）。

## Development Commands

```bash
# Setup (uv)
uv sync                    # 安装核心依赖
uv sync --all-extras       # 安装全部依赖（含 decrypt/summary 可选组）
cp .env.example .env

# Run (Gateway :8000 + Cortex :7999)
uv run main.py

# Run individually
uv run -m gateway.main     # Gateway only (:8000)
uv run -m cortex.main      # Cortex only (:7999)
uv run run_daemon.py       # Daemon (:8001)

# Tools
uv run tools/inspect_wechat.py   # 微信窗口检查
uv run tools/wechat_decrypt.py   # 微信数据库解密

# 依赖管理
uv add <package>           # 添加依赖
uv add --optional decrypt <package>  # 添加到 decrypt 可选组
uv remove <package>        # 移除依赖
uv lock                    # 重新生成 lock 文件
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
| Cortex  | 7999 | 业务逻辑（AI/命令/触发/Webhook/定时任务）+ Gateway API 代理 |

### Key Components

**common/** - 共享模块
- `config.py` - Pydantic Settings 配置 (全局 `config` 实例，从 .env 读取)
- `models.py` - WS 协议 Pydantic 模型 (discriminated union 解析, WSType 常量, model_validator 校验)
- `auth.py` - Bearer Token 验证 (HMAC 常量时间比较)
- `logging.py` - HealthFilter + setup_uvicorn_logging()

**gateway/** - Gateway 服务
- `runtime.py` - GatewayRuntime：自动窗口发现（5s 扫描），管理 detectors/sender/gateway
- `asgi.py` - FastAPI app 工厂，lifespan 管理生命周期
- `core/gateway.py` - Gateway WS hub：队列消费 + 正则匹配广播 + subscribe regex 校验
- `core/detector.py` - MessageDetector：per-group 消息检测线程，SHA256 上下文哈希去重
- `core/sender.py` - MessageSender：全局限速串行发送 (GLOBAL_SEND_INTERVAL)
- `adapters/base.py` - BaseWeChatAdapter 抽象基类 (4 个方法)
- `adapters/macos.py` - macOS: Quartz CGWindowListCreateImage 截图 + Vision OCR（无需激活窗口）
- `adapters/windows.py` - Windows: uiautomation 控件遍历，UIAutomationInitializerInThread per-thread 初始化，读取无锁并行，发送 _send_lock 串行
- `utils/ocr.py` - macOS Vision OCR 封装
- `utils/screenshot.py` - 截图工具
- `utils/accessibility.py` - macOS 辅助功能权限检查
- `storage/message_db.py` - SQLModel ORM 消息去重 (SQLite)
- `routes/` - REST (health, groups, messages, chatlog) + WS 端点 (`ws.py`)

**chatlog/** - 聊天记录模块（独立顶层模块）
- `__init__.py` - 导出 ChatlogService 和相关模型
- `service.py` - ChatlogService 统一接口类
- `models.py` - HTTP API 响应模型（ChatlogGroupResponse, ChatSummaryRequest 等）
- `wechat_db.py` - 微信数据库解密 + 消息查询（WeChatDBDecryptor, WeChatDBReader）
- `summary.py` - 群聊总结生成（LLM 总结 + Markdown → 图片渲染）
- `summary_service.py` - 总结服务（异步任务调度，SummaryConfig/SummaryResult）

**cortex/** - Cortex 服务
- `models.py` - TriggerType 枚举 + MessageListener 抽象基类（模板方法，异常由基类捕获）
- `runtime.py` - CortexRuntime (MessageListener)：先 trigger 匹配再滑动窗口限速 + ThreadPoolExecutor 处理 AI/命令
- `asgi.py` - FastAPI app 工厂，组装 listener 注册 + scheduler + lifespan
- `gateway_client.py` - GatewayClient：纯 WS 客户端，连接/重连/订阅/收发，add_listener 注册 MessageListener
- `scheduler.py` - TaskScheduler：轮询定时任务，通过 GatewayClient WS 发消息，HTTP 仅用于 groups/summary 查询
- `services/ai.py` - httpx.Client 连接池调用 OpenAI API
- `services/command.py` - httpx.Client 调用命令 API
- `services/trigger.py` - TriggerMatcher 正则匹配（AI/命令/文本/图片触发）
- `services/webhook.py` - WebhookService (MessageListener)：SQLModel ORM + ThreadPoolExecutor fire-and-forget
- `services/scheduled_task.py` - ScheduledTaskService：SQLModel ORM CRUD + cron 调度
- `routes/` - tasks, webhooks, health, gateway_proxy（代理 Gateway 的 /api/groups 和 /api/send）

**daemon/** - Daemon 控制器
- `manager.py` - BotProcessManager 进程管理（健康检查 + 自动重启）
- `ttyd_manager.py` - TtydProcessManager：ttyd 子进程启停，自动检测 shell（pwsh/powershell/zsh）
- `routes.py` - FastAPI 路由 (status/start/stop/restart/update/logs/ttyd)

**integrations/** - 外部 AI Agent 接入
- `nanobot/main.py` - NanoBot Agent WS 客户端（连接 Gateway，收到消息调 NanoBot AgentLoop 处理）
- `openclaw/index.js` - OpenClaw 本地 Channel Plugin（Node.js WS 客户端）
- `openclaw/openclaw.json` - OpenClaw 配置（指向本地插件）

## Key Patterns

- **MessageListener 模式**: 抽象基类 `on_message(msg: WSMessageBroadcast)` + `_handle_message()`，异常由基类统一捕获。GatewayClient 通过 `add_listener()` 注册多个 listener（CortexRuntime、WebhookService）
- **非阻塞 listener**: listener 回调在 GatewayClient async 线程中执行，禁止阻塞调用。CortexRuntime 用 `ThreadPoolExecutor` 处理 AI/命令，WebhookService 用 `ThreadPoolExecutor` 发 HTTP
- **纯 WS 客户端**: GatewayClient 只管连接/重连/订阅/收发，不持有业务逻辑。指数退避重连 (1s → 30s)，asyncio.Event 优雅停止
- **Gateway/Cortex 通信**: 所有消息收发走 WS 协议。Scheduler 发消息也通过 GatewayClient WS，不走 HTTP self-call
- **Cortex Gateway 代理**: Cortex 前端通过后端代理访问 Gateway API（/api/groups, /api/send），避免跨端口直连
- **macOS 截图**: Quartz `CGWindowListCreateImage` 按窗口 ID 内存截图，无需激活窗口，多群并行 OCR
- **macOS 发送**: 需要激活窗口（AppleScript AXRaise + 剪贴板粘贴），`_send_lock` 串行化
- **Windows UIA 线程**: `UIAutomationInitializerInThread` per-thread COM/UIA 初始化，读取无锁可并行，发送需 `_send_lock`
- **消息去重**: SHA256(前2条消息 + 当前消息 + 群名)，只触发窗口中最后一条消息
- **限速**: 全局发送间隔 `GLOBAL_SEND_INTERVAL`（默认 3s），群级触发限速 `TRIGGER_RATE_LIMIT`（默认 10次/60s 滑动窗口，在 trigger 匹配之后检查）
- **WS 首条消息认证**: 连接后首条消息必须是 `{"type": "auth", "token": "xxx"}`，`hmac.compare_digest` 常量时间比较，10s 超时断开。Token 不经过 URL，不留日志
- **WS 协议模型**: Pydantic discriminated union + TypeAdapter 一步解析，WSType 常量，WSSendMessage model_validator 校验
- **HTTP 客户端**: 全局使用 httpx（无 requests 依赖），服务内持久连接池
- **触发词空守卫**: `TRIGGER_KEYWORD` 为空时跳过 AI/刷新命令匹配（`startswith("")` 恒 True），命令匹配不受影响
- **comtypes 日志静默**: Windows 上 `comtypes` logger 设为 WARNING 级别，屏蔽 COM 类型库生成的 INFO 日志
- **WS 订阅优先级**: subscribe 支持 `priority`（int，默认 0）和 `passive`（bool，默认 false）。广播时 passive 客户端始终收到消息，非 passive 只推送给匹配的最高优先级客户端。Cortex 默认 priority=10
- **外部 Agent 接入**: NanoBot/OpenClaw 作为独立 WS 客户端连接 Gateway，与 Cortex 并行，通过 `message_filter` 分流消息，通过 `priority` 控制优先级
- **chatlog 回调解耦**: chatlog 模块不依赖 Gateway，通过 `SummaryConfig.on_group_complete: Callable[[GroupSummaryOutput], None]` 回调返回总结结果（图片 base64 + 文本），由调用方（gateway routes）决定如何投递消息。chatlog 必须部署在 Gateway 机器（需本地微信数据库访问）

## HTTP API

Gateway (:8000): `GET /api/health`, `GET /api/groups`, `POST /api/send`, `WS /ws`, chatlog 系列

Cortex (:7999): `GET /api/health`, CRUD `/api/tasks`, CRUD `/api/webhooks`, `GET /api/groups`(代理), `POST /api/send`(代理)

Daemon (:8001): `GET /status`, `POST /start|stop|restart|update`, `GET /logs`, `GET /ttyd/status`, `POST /ttyd/start|stop|restart`

认证: REST `Authorization: Bearer <HTTP_API_TOKEN>`，WS 首条消息认证 `{"type": "auth", "token": "xxx"}`(hmac.compare_digest)

## WS Protocol

连接: `ws://host:8000/ws`

Client → Server:
- `{"type": "auth", "token": "xxx"}` — 首条消息认证（10s 超时断开），成功返回 auth_ok
- `{"type": "subscribe", "group_filter": "regex", "message_filter": "regex", "priority": 0, "passive": false}` — priority/passive 可选，无效 regex 返回 error
- `{"type": "unsubscribe"}`
- `{"type": "send_message", "group_name": "...", "message": "...", "image_base64": "..."}`

Server → Client:
- `{"type": "auth_ok"}`
- `{"type": "subscribe_ok", ...}`
- `{"type": "message", "group_name": "...", "content": "...", "timestamp": ...}`
- `{"type": "send_ok"}`
- `{"type": "error", "detail": "..."}`

## Threading Model

- **Gateway 线程**: scan_loop(窗口发现) + detector(per-group, 读取无锁并行) + sender(串行发送) + consume_loop(队列广播)
- **GatewayClient 线程**: 独立 asyncio 事件循环，只做 WS 收发 + listener 分发（listener 回调必须非阻塞）
- **Cortex ThreadPoolExecutor(4)**: AI/命令等阻塞操作从 listener 回调提交到此线程池
- **Webhook ThreadPoolExecutor(3)**: HTTP POST fire-and-forget
- **Scheduler 线程**: 5s 轮询定时任务，阻塞操作在此线程内（不影响消息处理）
- **main.py**: asyncio.gather 运行双 server，异常时通知对方退出

## Development Notes

- **No test suite**: 项目当前没有配置 pytest / ruff / mypy 等工具，无自动化测试
- **Python 3.11+**: `requires-python = ">=3.11"`
- **uv 包管理**: 使用 uv 而非 pip，pyproject.toml 管理依赖，可选依赖组：`decrypt`、`summary`
- **配置**: Pydantic Settings 从 `.env` 读取，关键变量见 `.env.example`（`CHECK_INTERVAL`, `TRIGGER_*`, `OPENAI_*`, `DAEMON_*`, `WECHAT_DB_*`, `DEBUG`）

## Platform Notes

- **macOS**: 需要辅助功能权限（发送）+ 屏幕录制权限（截图）
- **Windows**: 建议管理员权限运行，UIA 使用 UIAutomationInitializerInThread per-thread 初始化
