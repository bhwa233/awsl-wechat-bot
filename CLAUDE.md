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

# Run (Gateway :8000 + Chatlog :7998 + Cortex :7999)
uv run main.py

# Run individually
uv run -m gateway.main     # Gateway only (:8000)
uv run -m chatlog.main     # Chatlog only (:7998)
uv run -m cortex.main      # Cortex only (:7999)
uv run run_daemon.py       # Daemon (:8001)

# Tools
uv run tools/inspect_wechat.py   # 微信窗口检查
uv run tools/extract_wx_keys_windows.py  # Windows 提取微信数据库密钥
sudo uv run tools/extract_wx_keys_linux.py  # Linux 提取微信数据库密钥
uv run --with pycryptodome tools/extract_wx_image_key_windows.py  # Windows 派生图片 V2 .dat AES key
uv run --with pycryptodome tools/extract_wx_image_key_linux.py    # Linux 派生图片 V2 .dat AES key (无需 root)

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
                    ├── send_message via WS ──▶ Gateway ──▶ Sender
                    │
                    └── HTTP ──▶ Chatlog Service (:7998)
                                (解密/查群/查消息)
```

### Services

| Service | Port | Role |
|---------|------|------|
| Gateway | 8000 | WS 消息网关 + REST API + 微信窗口交互 |
| Chatlog | 7998 | 微信数据库解密/查询（独立数据层服务） |
| Daemon  | 8001 | Bot 子进程管理（启停/更新） |
| Cortex  | 7999 | 业务逻辑（AI/命令/触发/Webhook/定时任务/群聊总结）+ Gateway/Chatlog API 代理 |

### Key Components

**common/** - 共享模块
- `config.py` - Pydantic Settings 配置 (全局 `config` 实例，从 .env 读取)
- `models.py` - WS 协议 Pydantic 模型 (discriminated union 解析, WSType 常量, model_validator 校验, MAX_REGEX_LEN 共享常量)
- `auth.py` - Bearer Token 验证 (HMAC 常量时间比较)
- `logging.py` - HealthFilter + setup_uvicorn_logging()

**gateway/** - Gateway 服务
- `runtime.py` - GatewayRuntime：自动窗口发现（5s 扫描），管理 detectors/sender/gateway
- `asgi.py` - FastAPI app 工厂，lifespan 管理生命周期
- `core/gateway.py` - Gateway WS hub：队列消费 + 正则匹配广播 + subscribe regex 校验
- `core/detector.py` - MessageDetector：per-group 消息检测线程，SHA256 上下文哈希去重
- `core/models.py` - SendTask + SentMessageStore（自消息检测：精确/前缀/包含/相似度 4 层匹配）
- `core/sender.py` - MessageSender：全局限速串行发送 (GLOBAL_SEND_INTERVAL)
- `adapters/base.py` - BaseWeChatAdapter 抽象基类 (窗口读取/发送 + 平台消息检测策略)
- `adapters/macos.py` - macOS: Quartz CGWindowListCreateImage 截图 + Vision OCR（无需激活窗口）
- `adapters/windows.py` - Windows: uiautomation 控件遍历，UIAutomationInitializerInThread per-thread 初始化，读取无锁并行，发送 _send_lock 串行
- `adapters/linux.py` - Linux: 通过 Chatlog HTTP 读取文本消息，通过 X11 工具发送文本/图片/文件
- `utils/ocr.py` - macOS Vision OCR 封装
- `utils/screenshot.py` - 截图工具
- `utils/accessibility.py` - macOS 辅助功能权限检查
- `storage/message_db.py` - SQLModel ORM 消息去重 (SQLite)
- `routes/` - REST (health, groups, messages) + WS 端点 (`ws.py`)

**chatlog/** - 聊天记录独立服务（纯数据层，不依赖 Gateway/Cortex）
- `__init__.py` - 导出 ChatlogService 和相关模型
- `asgi.py` - FastAPI app 工厂（v1/v2 双前缀挂载 + 默认无版本路径按 `CHATLOG_USE_V2` 切换）
- `main.py` - 独立入口（`uv run -m chatlog.main`）
- `routes.py` - v1 HTTP 路由（decrypt/groups/messages/group-display-name/health/media，挂载在 `/api/chatlog/v1`）
- `routes_v2.py` - v2 HTTP 路由（同 v1 接口集合，挂载在 `/api/chatlog/v2`，Windows/Linux）
- `service.py` - v1 ChatlogService（pycryptodome 离线解密）
- `service_v2.py` - v2 ChatlogServiceV2（原生库在线读取）
- `models.py` - HTTP API 响应模型（`ChatlogGroupResponse`, `ChatlogMessageResponse`；`server_id: int` + `server_id_str: str` 双字段避免 JS Number 精度丢失）
- `wechat_db.py` - v1 实现（WeChatDBDecryptor + WeChatDBReader），含 ChatRoom/Message dataclass 和 protobuf 解析；解密扩展覆盖 message_resource.db / media_0.db / emoticon.db
- `wechat_db_v2.py` - v2 实现（WeChatDBReaderV2），通过原生库跑 raw SQL，复用 v1 的 dataclass 和 protobuf 解析
- `media/` - 富媒体解析层（图片 V2 .dat / sticker / voice / video thumb 统一抽象）
  - `route.py` - `MediaBackendBinding` (V1/V2) + `register_media_route()` 注册 GET `/media`；image/video-thumb 强制 `IMAGE_AES_KEY`，voice/sticker/file 不依赖；file 响应附 `Content-Disposition` (UTF-8 + ASCII fallback)
  - `adapters.py` - `MediaAdapter` ABC + `V1SqliteAdapter` (sqlite3 直读) + `V2NativeAdapter` (原生库 + SQL 占位手动转义，db_path → key LRU 缓存 32)
  - `resolver.py` - `MediaResolver`：`decode_image` / `decode_video_thumb` / `decode_voice` / `decode_sticker`
  - `cache.py` - 进程级共享缓存：sticker / hd_image 各 50MB 字节预算 LRU；emoticon_lookup OrderedDict LRU 32
  - `decrypt.py` - V2 .dat AES-128-ECB + XOR magic + 尾对齐块解密
  - `wxgf.py` - PyAV 取末帧将 wxgf (HEVC) 转 JPEG
  - `voice.py` - pilk + PyAV 将 SILK v3 转 MP3
- `platform/` - Chatlog 平台特性封装（Windows/Linux 原生库加载、数据库路径解析、配置检查、Linux per-db key 解析）
- `vendor/awsl_wechat_chatlog.dll` - awsl-wechat-chatlog 编译产物（11MB，Windows x86-64）
- `vendor/libawsl_wechat_chatlog.so` - awsl-wechat-chatlog 编译产物（Linux x86-64）

**cortex/** - Cortex 服务
- `models.py` - TriggerType 枚举 + MessageListener 抽象基类（模板方法，异常由基类捕获）
- `runtime.py` - CortexRuntime (MessageListener)：先 trigger 匹配再滑动窗口限速 + ThreadPoolExecutor 处理 AI/命令
- `asgi.py` - FastAPI app 工厂，组装 listener 注册 + scheduler + lifespan
- `gateway_client.py` - GatewayClient：纯 WS 客户端，连接/重连/订阅/收发，add_listener 注册 MessageListener
- `scheduler.py` - TaskScheduler：轮询定时任务，通过 GatewayClient WS 发消息，summary 任务本地异步执行
- `services/ai.py` - httpx.Client 连接池调用 OpenAI API
- `services/command.py` - httpx.Client 调用命令 API
- `services/trigger.py` - TriggerMatcher 正则匹配（AI/命令/文本/图片触发）
- `services/webhook.py` - WebhookService (MessageListener)：SQLModel ORM + ThreadPoolExecutor fire-and-forget
- `services/scheduled_task.py` - ScheduledTaskService：SQLModel ORM CRUD + cron 调度
- `services/chatlog_client.py` - ChatlogClient：httpx.Client 调用 Chatlog 服务 API
- `services/summary.py` - 群聊总结核心算法（格式化/排行榜/LLM 调用/Markdown→图片渲染）
- `services/summary_service.py` - SummaryService：调 chatlog 拉消息，本地 LLM + 渲染，通过 gateway_client WS 发出
- `routes/` - tasks, webhooks, health（含 webui 入口 `/` + 静态资源 `/style.css|/app.js|/chat.js|/tasks.js|/webhooks.js|/manifest.json`）, summary, gateway_proxy（代理 Gateway API）, chatlog_proxy（代理 Chatlog API）
- `templates/` - Cortex Web UI（SPA：聊天/定时任务/Webhook 三个 tab，由 `routes/health.py` 提供静态文件服务）

**daemon/** - Daemon 控制器
- `manager.py` - BotProcessManager 进程管理（健康检查 + 自动重启）
- `routes.py` - FastAPI 路由 (status/start/stop/restart/update/logs)

## Key Patterns

- **MessageListener 模式**: 抽象基类 `on_message(msg: WSMessageBroadcast)` + `_handle_message()`，异常由基类统一捕获。GatewayClient 通过 `add_listener()` 注册多个 listener（CortexRuntime、WebhookService）
- **非阻塞 listener**: listener 回调在 GatewayClient async 线程中执行，禁止阻塞调用。CortexRuntime 用 `ThreadPoolExecutor` 处理 AI/命令，WebhookService 用 `ThreadPoolExecutor` 发 HTTP
- **纯 WS 客户端**: GatewayClient 只管连接/重连/订阅/收发，不持有业务逻辑。指数退避重连 (1s → 30s)，asyncio.Event 优雅停止
- **Gateway/Cortex 通信**: 所有消息收发走 WS 协议。Scheduler 发消息也通过 GatewayClient WS，不走 HTTP self-call
- **Cortex Gateway 代理**: Cortex 前端通过后端代理访问 Gateway API（/api/groups, /api/send），避免跨端口直连
- **macOS 截图**: Quartz `CGWindowListCreateImage` 按窗口 ID 内存截图，无需激活窗口，多群并行 OCR
- **macOS 发送**: 需要激活窗口（AppleScript AXRaise + 剪贴板粘贴），`_send_lock` 串行化
- **Windows UIA 线程**: `UIAutomationInitializerInThread` per-thread COM/UIA 初始化，读取无锁可并行，发送需 `_send_lock`
- **Linux Chatlog 读取**: Gateway 不做截图/OCR，通过 Chatlog HTTP 查询最近文本消息；发送依赖 X11 工具 `wmctrl`、`xdotool`、`xclip`、`xwininfo`，推荐 `xsel`
- **消息去重**: SHA256(前2条消息 + 当前消息 + 群名)，只触发窗口中最后一条消息
- **限速**: 全局发送间隔 `GLOBAL_SEND_INTERVAL`（默认 3s），群级触发限速 `TRIGGER_RATE_LIMIT`（默认 10次/60s 滑动窗口，在 trigger 匹配之后检查）
- **WS 首条消息认证**: 连接后首条消息必须是 `{"type": "auth", "token": "xxx"}`，`hmac.compare_digest` 常量时间比较，10s 超时断开。Token 不经过 URL，不留日志
- **WS 协议模型**: Pydantic discriminated union + TypeAdapter 一步解析，WSType 常量，发送载荷统一为 `message_type + message + filename`
- **HTTP 客户端**: 全局使用 httpx（无 requests 依赖），服务内持久连接池，AI service 限制 max_connections=10
- **自消息过滤**: Windows UIA 读取所有消息（含自己发送的），SentMessageStore 通过精确/前缀/包含/相似度 4 层匹配过滤自发消息，TTL 120s
- **输入校验**: 正则长度 MAX_REGEX_LEN=200（common/models.py 共享），cron 表达式限 256 字符，AppleScript 参数化防注入（`on run argv`）
- **触发词空守卫**: `TRIGGER_KEYWORD` 为空时跳过 AI/刷新命令匹配（`startswith("")` 恒 True），命令匹配不受影响
- **comtypes 日志静默**: Windows 上 `comtypes` logger 设为 WARNING 级别，屏蔽 COM 类型库生成的 INFO 日志
- **WS 订阅优先级**: subscribe 支持 `priority`（int，默认 0）和 `passive`（bool，默认 false）。广播时 passive 客户端始终收到消息，非 passive 只推送给匹配的最高优先级客户端。Cortex 默认 priority=10
- **chatlog 独立服务**: chatlog 是纯数据层独立服务（:7998），不依赖 Gateway/Cortex。提供解密/查群/查消息 HTTP API。Cortex 通过 ChatlogClient 调用 chatlog 拉消息，本地跑 LLM 总结 + 渲染，再通过 gateway_client WS 发出。chatlog 需部署在微信数据库所在机器

## HTTP API

Gateway (:8000): `GET /api/health`, `GET /api/groups`, `POST /api/send`, `WS /ws`

Chatlog (:7998):
- v1 (pycryptodome 离线解密): `GET /api/chatlog/v1/health`, `POST /api/chatlog/v1/decrypt`, `GET /api/chatlog/v1/groups`, `GET /api/chatlog/v1/messages`, `GET /api/chatlog/v1/group-display-name`, `GET /api/chatlog/v1/media`
- v2 (原生库在线读取，Windows/Linux): `GET /api/chatlog/v2/health`, `GET /api/chatlog/v2/groups`, `GET /api/chatlog/v2/messages`, `GET /api/chatlog/v2/group-display-name`, `GET /api/chatlog/v2/media`（v2 也有 `/decrypt` 但是 no-op，原生库直接读密文）
- 默认（无版本前缀）`/api/chatlog/*`: 由 `CHATLOG_USE_V2` 切换（默认 true→v2，false→v1）
- 媒体端点 `GET /media?msg_type=&group=&server_id=&content=&hd=`: msg_type=3 image / 34 voice / 43 video thumb / 47 sticker / 49 file。image/video-thumb 需要 `IMAGE_AES_KEY`；voice 需 `--extra media`；sticker/file 不依赖密钥。file 走 `msg/file/<YYYY-MM>/<title>` 明文 + `Content-Disposition` (UTF-8 + ASCII fallback) 浏览器原文件名下载，上限 200MB。返回原始 bytes + `X-Media-Md5` / `X-Media-Format` / `Cache-Control: private, max-age=86400, immutable` / `ETag`
- 健康端点 `media` 字段：`{image, sticker, video_thumb, voice, file}` 各 bool，前端据此启用渲染

Cortex (:7999):
- Web UI: `GET /` (HTML), `GET /style.css|/app.js|/chat.js|/tasks.js|/webhooks.js|/manifest.json`
- API: `GET /api/health`, CRUD `/api/tasks`, CRUD `/api/webhooks`, `POST /api/summary`, `GET /api/groups`(代理 Gateway), `POST /api/send`(代理 Gateway), `GET /api/chatlog/groups`(代理 Chatlog), `GET /api/chatlog/messages`(代理 Chatlog), `POST /api/chatlog/decrypt`(代理 Chatlog), `GET /api/chatlog/health`(代理 Chatlog 无版本健康端点，含 media 能力), `GET /api/chatlog/media`(代理 Chatlog 富媒体)

Daemon (:8001): `GET /status`, `POST /start|stop|restart|update`, `GET /logs`

认证: REST `Authorization: Bearer <HTTP_API_TOKEN>`，WS 首条消息认证 `{"type": "auth", "token": "xxx"}`(hmac.compare_digest)

## WS Protocol

连接: `ws://host:8000/ws`

Client → Server:
- `{"type": "auth", "token": "xxx"}` — 首条消息认证（10s 超时断开），成功返回 auth_ok
- `{"type": "subscribe", "group_filter": "regex", "message_filter": "regex", "priority": 0, "passive": false}` — priority/passive 可选，无效 regex 返回 error
- `{"type": "unsubscribe"}`
- `{"type": "send_message", "group_name": "...", "message_type": "text|image|file", "message": "...", "filename": "..."}`（file 需要 filename）

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
- **Scheduler 线程**: 5s 轮询定时任务，summary 任务异步提交到 SummaryService worker 线程（不阻塞调度）
- **Summary worker 线程**: daemon 线程，调 ChatlogClient HTTP 拉消息 + LLM + 渲染 + gateway_client WS 发出
- **Chatlog 服务**: 独立 uvicorn 进程，sync def 路由（FastAPI 自动线程池），无外部出站网络
- **main.py**: asyncio.gather 运行三个 server（Gateway + Chatlog + Cortex），异常时通知退出

## Development Notes

- **No test suite**: 项目当前没有配置 pytest / ruff / mypy 等工具，无自动化测试
- **Python 3.11+**: `requires-python = ">=3.11"`
- **uv 包管理**: 使用 uv 而非 pip，pyproject.toml 管理依赖，可选依赖组：`decrypt`、`summary`
- **配置**: Pydantic Settings 从 `.env` 读取，关键变量见 `.env.example`（`CHECK_INTERVAL`, `TRIGGER_*`, `OPENAI_*`, `DAEMON_*`, `WECHAT_DB_*`, `CHATLOG_USE_V2`, `LINUX_WECHAT_*`, `LINUX_CHATLOG_*`, `DEBUG`）

## Platform Notes

- **macOS**: 需要辅助功能权限（发送）+ 屏幕录制权限（截图）
- **Windows**: 建议管理员权限运行，UIA 使用 UIAutomationInitializerInThread per-thread 初始化
- **Linux**: 需要 Linux WeChat、可访问的 X11 会话、Chatlog 服务和 `wmctrl`/`xdotool`/`xclip`/`xwininfo`；文本发送优先使用 `xsel`
