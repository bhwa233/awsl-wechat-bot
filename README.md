# AWSL 微信机器人

> ⚠️ **免责声明：本项目仅供学习交流，请勿用于商业或违规用途。使用本项目产生的任何后果由使用者自行承担。**

基于 macOS Accessibility API 和 Windows UI Automation 的微信群聊机器人，支持 AI 智能回答、动态命令执行、定时任务和群聊总结。

## 功能特性

- **跨平台支持**：支持 macOS 和 Windows 10/11 平台
- **原生消息读取**：
  - macOS: 使用 Accessibility API（暂不可用，无法正常读取消息）
  - Windows: 使用 UI Automation 直接读取控件（100% 准确）
- **多群并发处理**：每群独立检测线程 + ThreadPoolExecutor 并发任务处理
- **HTTP API 服务**：提供 RESTful API 接口和 Web 管理后台
- **定时任务系统**：支持 Cron 表达式的定时消息发送
- **群聊总结功能**：自动解密微信数据库、AI 生成聊天总结并发送图片
- **@机器人触发**：支持 `@机器人名称` 方式触发 AI 回复（支持多个名称）
- 智能消息去重机制，基于上下文的哈希算法
- 动态命令系统，从远程 API 加载和执行自定义命令
- SQLite 数据库去重，避免重复响应
- Bearer Token API 认证
- 使用 Pydantic Settings 管理配置

## 系统要求

### macOS
- macOS 10.15+ (Catalina 或更高版本)
- 微信 macOS 客户端
- 辅助功能权限（系统设置 → 隐私与安全性 → 辅助功能）

### Windows
- Windows 10 / 11
- 微信 Windows 客户端
- 建议以管理员身份运行终端

### 共同要求
- Python 3.9+

## 安装

1. 克隆项目并进入目录：

```bash
cd awsl-wechat-bot
```

2. 创建虚拟环境并安装依赖：

**macOS:**
```bash
python3 -m venv venv
./venv/bin/python -m pip install -r requirements.txt
```

**Windows:**
```bash
python -m venv venv
./venv/Scripts/python -m pip install -r requirements.txt
```

## 配置

复制配置模板文件并编辑：
```bash
cp .env.example .env
```

### 基础配置

```env
# 群聊名称（macOS 单群模式使用）
GROUP_NAME=你的群聊名称

# 触发关键词
TRIGGER_KEYWORD=awsl

# 机器人名称列表（@名称 触发 AI 回复，逗号分隔，可选）
BOT_NAMES=机器人,小助手

# OpenAI API 配置（必填）
OPENAI_API_KEY=your-api-key-here
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini

# 命令 API 地址（可选）
COMMAND_API_BASE_URL=https://your-api-domain.com/api
```

### HTTP API 配置

```env
# HTTP API 服务配置
HTTP_API_HOST=0.0.0.0
HTTP_API_PORT=8000
HTTP_API_TOKEN=your-secret-token-here  # Bearer Token 认证，留空则不启用
HTTP_API_BASE_URL=http://localhost:8000  # 内部回调地址
```

### 微信数据库配置（群聊总结功能）

```env
# 微信数据库解密/总结配置（可选）
WECHAT_DB_INPUT_PATH=/path/to/wechat/db   # 加密数据库输入目录
WECHAT_DB_OUTPUT_PATH=/path/to/output     # 解密后输出目录
WECHAT_DB_KEY=your-decrypt-key            # 数据库解密密钥
```

## 使用方法

### 1. 前置准备

1. 启动微信并登录
2. 权限设置：
   - **macOS**: 系统设置 → 隐私与安全性 → 辅助功能，添加终端应用
   - **Windows**: 建议以管理员身份运行

### 2. 运行机器人

**macOS:**
```bash
./venv/bin/python main.py
```

**Windows:**
```bash
./venv/Scripts/python main.py
```

### 3. 触发方式

| 触发方式 | 示例 | 说明 |
|---------|------|------|
| 关键词+问题 | `awsl 今天天气怎么样？` | 触发 AI 回复 |
| @机器人 | `@机器人 帮我写首诗` | 触发 AI 回复（需配置 BOT_NAMES） |
| 命令帮助 | `awsl hp` | 显示可用命令列表 |
| 动态命令 | `<命令名> <参数>` | 执行远程命令（无需前缀） |

### 4. HTTP API

启动后访问 Web 管理后台：http://localhost:8000

**主要 API 端点：**

```bash
# 健康检查
GET /api/health

# 群组管理
GET /api/groups

# 发送消息
POST /api/send
{
  "group_name": "群聊名称",
  "message": "消息内容"
}

# 发送图片
POST /api/send
{
  "group_name": "群聊名称",
  "image_base64": "base64编码的图片"
}

# 定时任务
GET /api/tasks
POST /api/tasks
PUT /api/tasks/{id}
DELETE /api/tasks/{id}

# 聊天记录（需配置微信数据库）
POST /api/chatlog/decrypt      # 解密数据库
GET /api/chatlog/groups        # 列出群聊
GET /api/chatlog/messages      # 查询消息
POST /api/chatlog/summary      # 发送群聊总结
```

**认证方式：**
```bash
curl -H "Authorization: Bearer your-token" http://localhost:8000/api/groups
```

**API 文档：** http://localhost:8000/docs

## 架构设计

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

### 核心组件

| 组件 | 文件 | 职责 |
|------|------|------|
| Bot | `src/bot.py` | 主机器人类，组件编排 |
| Detector | `src/core/detector.py` | 消息检测器，每群一个线程 |
| Dispatcher | `src/core/dispatcher.py` | 任务分发器，并发处理 |
| Sender | `src/core/sender.py` | 消息发送器，全局限速 |
| TriggerMatcher | `src/core/trigger.py` | 触发词匹配逻辑 |

### 平台适配器

- `src/adapters/macos.py` - macOS Accessibility API
- `src/adapters/windows.py` - Windows UI Automation

### HTTP 服务

- `src/services/http/server.py` - FastAPI 服务
- `src/services/http/routes/` - API 路由
- `src/services/http/templates/index.html` - Web 管理后台

## 项目结构

```
awsl-wechat-bot/
├── main.py                    # 主程序入口
├── daemon.py                  # Daemon 控制器（管理 bot 子进程）
├── config.py                  # 配置文件（Pydantic Settings）
├── src/
│   ├── bot.py                 # 机器人主类
│   ├── adapters/              # 平台适配器
│   │   ├── base.py            # 适配器基类
│   │   ├── macos.py           # macOS 实现
│   │   └── windows.py         # Windows 实现
│   ├── core/                  # 核心组件
│   │   ├── detector.py        # 消息检测器
│   │   ├── dispatcher.py      # 任务分发器
│   │   ├── sender.py          # 消息发送器
│   │   └── trigger.py         # 触发匹配器
│   ├── models/                # 数据模型
│   │   └── tasks.py           # 任务模型
│   ├── services/              # 服务模块
│   │   ├── ai.py              # AI 服务
│   │   ├── command.py         # 动态命令服务
│   │   └── http/              # HTTP API 服务
│   ├── storage/               # 存储模块
│   │   └── message_db.py      # SQLite 消息去重
│   └── utils/                 # 工具模块
│       ├── wechat_chatlog.py  # 微信数据库读取
│       └── summary_service.py # 群聊总结服务
├── tools/                     # 调试工具
│   ├── chat_summary.py        # 群聊总结命令行工具
│   ├── wechat_decrypt.py      # 微信数据库解密工具
│   └── inspect_wechat.py      # 微信窗口检查工具
├── .env.example               # 环境变量配置模板
└── requirements.txt           # Python 依赖
```

## Daemon 控制器

Daemon 是一个独立的 HTTP 服务，用于远程管理 bot 的启停和代码更新。

```
daemon.py (port 8001, 常驻 HTTP API)
  └─ main.py (bot 子进程, port 8000)
```

### 运行

```bash
./venv/Scripts/python daemon.py
```

### Daemon API 端点

所有端点需要 Bearer Token 认证（复用 `HTTP_API_TOKEN`）。

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/status` | bot 运行状态、PID、uptime、重启次数 |
| POST | `/start` | 启动 bot |
| POST | `/stop` | 停止 bot |
| POST | `/restart` | 重启 bot |
| POST | `/update` | git pull + pip install + 重启 bot |
| GET | `/logs?lines=100` | 读取 bot.log 最后 N 行 |

### Daemon 配置

在 `.env` 中添加（均有默认值，可选）：

```env
DAEMON_PORT=8001
DAEMON_HOST=0.0.0.0
DAEMON_RESTART_DELAY=5
DAEMON_MAX_RETRIES=10
DAEMON_LOG_BUFFER_LINES=5000
```

## 注意事项

- 首次运行需要授予相关权限
- 运行时微信窗口需要保持可见（不能最小化）
- 建议在虚拟机中运行，实现隔离和 24/7 运行
- 生产环境关闭 DEBUG 模式以减少日志输出

## 免责声明

**本项目仅供学习交流使用，请勿用于商业用途或任何违法违规行为。**

- 本项目是技术学习和研究项目
- 使用时请遵守微信使用条款和相关法律法规
- 开发者不对使用本项目造成的任何后果负责
- 请勿用于发送垃圾信息、骚扰他人或传播违法内容

## 许可证

MIT License
