# AWSL 微信机器人

> **免责声明：本项目仅供学习交流，请勿用于商业或违规用途。使用本项目产生的任何后果由使用者自行承担。**

跨平台微信群聊机器人，采用 Gateway + Cortex 微服务架构。Gateway 通过 Quartz/UIAutomation 与微信窗口交互，以 WebSocket 协议广播消息；Cortex 作为 WS 客户端，通过 MessageListener 模式处理 AI 回复、动态命令、定时任务和 Webhook 转发。

## 功能特性

- **跨平台支持**：macOS (Quartz 截图 + Vision OCR) / Windows (UI Automation)
- **自动窗口发现**：自动扫描微信群聊窗口，动态增删检测线程
- **WebSocket 消息网关**：统一消息广播，支持正则过滤订阅
- **多群并发处理**：每群独立检测线程，全局限速发送
- **定时任务 / 群聊总结 / @触发 / 动态命令 / Webhook 转发**
- **外部 Agent 接入**：NanoBot (Python) / OpenClaw (Node.js) 作为 WS 客户端并行运行

<details>
<summary>架构设计</summary>

```
┌──────────────────────────────────────────────────────┐
│                    Gateway (:8000)                    │
│                                                      │
│  Detector (n) ──▶ gateway_queue ──▶ Gateway (WS)     │
│  per group          (Queue)          │  广播          │
│                                ┌─────┼─────┐         │
│  Window Scanner                │     │     │         │
│  (自动发现窗口)                ▼     ▼     ▼         │
│                            WS Client A  B  C ...     │
│  MessageSender ◀── send_message ◀──┘                 │
│  (全局限速)                                          │
└──────────────────────────────────────────────────────┘
                                 │
                    ┌────────────┘
                    ▼
┌──────────────────────────────────────────────────────┐
│                    Cortex (:7999)                     │
│                                                      │
│  GatewayClient (纯 WS 客户端, 指数退避重连)          │
│    │  add_listener()                                 │
│    ├── CortexRuntime (MessageListener)               │
│    │     冷却/触发匹配(同步) → ThreadPool(AI/命令)    │
│    └── WebhookService (MessageListener)              │
│          正则过滤 → ThreadPool(HTTP POST)             │
│                                                      │
│  TaskScheduler → GatewayClient.send_message (WS)     │
└──────────────────────────────────────────────────────┘
```

</details>

<details>
<summary>线程模型</summary>

| 线程 | 职责 | 阻塞安全 |
|------|------|----------|
| Gateway scan_loop | 窗口自动发现 (5s) | 独立线程 ✅ |
| Detector (per-group) | OCR 截图 + 消息检测 | 独立线程 ✅ |
| Sender | 串行发送 + 全局限速 | 独立线程 ✅ |
| Gateway consume_loop | 队列消费 + WS 广播 | 独立线程 ✅ |
| GatewayClient | async WS 收发 + listener 分发 | **禁止阻塞** ⚠️ |
| Cortex ThreadPool(4) | AI/命令等阻塞操作 | 独立线程 ✅ |
| Webhook ThreadPool(3) | HTTP POST fire-and-forget | 独立线程 ✅ |
| Scheduler | 定时任务轮询 (5s) | 独立线程 ✅ |

</details>

## 系统要求

- Python 3.11+
- **macOS**: macOS 10.15+，微信客户端，辅助功能权限 + 屏幕录制权限
- **Windows**: Windows 10/11，微信客户端，建议管理员身份运行

## 安装

```bash
cd awsl-wechat-bot

# 安装依赖（需要 uv: https://docs.astral.sh/uv/）
uv sync                    # 核心依赖
uv sync --all-extras       # 全部依赖（含解密/总结可选组）

cp .env.example .env  # 编辑配置
```

## 配置

### 基础配置 (.env)

```env
# 触发关键词
TRIGGER_KEYWORD=awsl

# 机器人名称列表（@触发，逗号分隔）
BOT_NAMES=机器人,小助手

# OpenAI API
OPENAI_API_KEY=your-api-key-here
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini

# 服务端口
HTTP_API_PORT=8000
HTTP_API_TOKEN=your-secret-token  # 留空不启用认证
CORTEX_HTTP_PORT=7999
TTYD_HOST=0.0.0.0
TTYD_PORT=7681
TTYD_CREDENTIALS=
```

## 运行

```bash
# 一体启动（Gateway + Cortex）
uv run main.py

# 独立启动
uv run -m gateway.main     # Gateway only (:8000)
uv run -m cortex.main      # Cortex only (:7999)
uv run run_daemon.py       # Daemon (:8001)
```

### 触发方式

| 触发方式 | 示例 | 说明 |
|---------|------|------|
| 关键词+问题 | `awsl 今天天气怎么样？` | 触发 AI 回复 |
| @机器人 | `@机器人 帮我写首诗` | 触发 AI 回复 |
| 命令帮助 | `awsl hp` | 显示可用命令列表 |
| 动态命令 | `<命令名> <参数>` | 执行远程命令 |

<details>
<summary>API 文档</summary>

### Gateway (:8000)

```
GET  /api/health              # 健康检查
GET  /api/groups              # 群组列表
POST /api/send                # 发送消息/图片
WS   /ws?token=<token>        # WebSocket 消息订阅
POST /api/chatlog/decrypt     # 解密微信数据库
GET  /api/chatlog/groups      # 群聊列表
GET  /api/chatlog/messages    # 查询消息
POST /api/chatlog/summary     # 群聊总结
```

### Cortex (:7999)

```
GET  /api/health                      # 健康检查
GET/POST/PUT/DELETE /api/tasks        # 定时任务 CRUD
GET/POST/PUT/DELETE /api/webhooks     # Webhook 管理
```

### Daemon (:8001)

```
GET  /status              # 运行状态
POST /start|stop|restart  # 启停控制
POST /update              # git pull + uv sync + 重启
GET  /logs?lines=100      # 日志
GET  /ttyd/status         # ttyd 状态
POST /ttyd/start          # 启动 ttyd (默认 pwsh，回退 powershell，可选 -c 鉴权)
POST /ttyd/stop           # 停止 ttyd
POST /ttyd/restart        # 重启 ttyd
```

注意：当 `TTYD_HOST=0.0.0.0` 且未额外加反向代理或防火墙限制时，`ttyd` 是裸终端暴露，任何能访问该端口的人都可直接获得 shell。
如需启用 ttyd 基础鉴权，可配置 `TTYD_CREDENTIALS=user:pass`。
Windows 下为保证 `pwsh/powershell` 可正常交互，daemon 会优先用 `winpty` 包装；如果缺少 `winpty`，启动 ttyd 会直接报错提示安装。

### WebSocket 协议

连接：`ws://host:8000/ws?token=<bearer_token>`

**Client → Server：**
```json
{"type": "subscribe", "group_filter": "regex", "message_filter": "regex"}
{"type": "unsubscribe"}
{"type": "send_message", "group_name": "群名", "message": "text", "image_base64": "..."}
```

**Server → Client：**
```json
{"type": "subscribe_ok", "group_filter": "...", "message_filter": "..."}
{"type": "message", "group_name": "群名", "content": "消息内容", "timestamp": 1710000000.0}
{"type": "send_ok"}
{"type": "error", "detail": "..."}
```

认证：`Authorization: Bearer <HTTP_API_TOKEN>`

</details>

## 外部 AI Agent 接入（可选）

Gateway 的 WS 协议支持任意客户端接入。`integrations/` 目录提供了两种 AI Agent 框架的集成：

| Agent | 语言 | 启动 |
|-------|------|------|
| NanoBot | Python | `cd integrations/nanobot && uv sync && uv run main.py` |
| OpenClaw | Node.js | `cd integrations/openclaw && npm install && openclaw --config openclaw.json` |

它们与 Cortex 并行运行，通过 `message_filter` 正则分流消息。详见 [integrations/README.md](integrations/README.md)。

## 注意事项

- macOS 首次运行需授予辅助功能权限（发送）和屏幕录制权限（截图 OCR）
- 运行时微信群聊窗口需保持打开（弹出的独立聊天窗口）
- 窗口自动发现，无需手动选择群聊

## 免责声明

**本项目仅供学习交流使用，请勿用于商业用途或任何违法违规行为。**

- 使用时请遵守微信使用条款和相关法律法规
- 开发者不对使用本项目造成的任何后果负责

## 许可证

MIT License
