# HTTP API、WebSocket 与监控

[返回 README](../README.md)

## Gateway (:8000)

```
GET  /api/health              # 健康检查
GET  /api/groups              # 群组列表
POST /api/send                # 发送文本/图片/文件/语音
POST /api/send/file           # Stream a file, then queue it for sending
WS   /ws                      # WebSocket（首条消息认证）
```

`POST /api/send/file?group_name=demo&filename=report.pdf` accepts a raw request body (for example, `application/octet-stream`). The filename must be a portable basename of at most 200 UTF-8 bytes; invalid names return 400. Uploads stream directly into one final file in the system temporary directory, without Base64 conversion or multipart spooling.

Limits: 100 MiB per file, 256 MiB of temporary files owned by the current Gateway process, five concurrent uploads, and a 300-second upload timeout. HTTP 200 means the complete file has entered the send queue; oversized files return 413, a sixth concurrent upload returns 429, and exhausted storage or queue capacity returns 503. A full send queue is handled by the existing enqueue result after reception; rejected files are removed. Files left behind by a previous Gateway process are outside the new process's quota.

The existing HTTP/WS Base64 interfaces and limits remain unchanged. Base64 keeps its existing decode, temporary-file, and send entry points. The API owns streamed-file reception, storage accounting, and cleanup. The existing queue carries the completed path; the sender only reports its send result through a Future, without a cleanup callback. API background tasks wait for that result and schedule deletion without blocking requests. Adapters send that path through their existing paste/send helpers. After successful sends, cleanup waits 60 seconds on Windows and 180 seconds on Linux, and runs immediately on macOS. Deletion failures are retried without releasing the reserved capacity. Interrupted uploads and files rejected by the send queue are cleaned up immediately.

## Chatlog (:7998)

```
# v1 (pycryptodome 离线解密)
GET  /api/chatlog/v1/health
POST /api/chatlog/v1/decrypt
GET  /api/chatlog/v1/groups
GET  /api/chatlog/v1/contacts
GET  /api/chatlog/v1/members
GET  /api/chatlog/v1/messages
POST /api/chatlog/v1/messages/search
POST /api/chatlog/v1/messages/count
GET  /api/chatlog/v1/group-display-name
GET  /api/chatlog/v1/media

# v2 (原生库在线读 SQLCipher v4，Windows/Linux)
GET  /api/chatlog/v2/health
POST /api/chatlog/v2/decrypt       # no-op，v2 原生库直接读密文
GET  /api/chatlog/v2/groups
GET  /api/chatlog/v2/contacts
GET  /api/chatlog/v2/members
GET  /api/chatlog/v2/messages
POST /api/chatlog/v2/messages/search
POST /api/chatlog/v2/messages/count
POST /api/chatlog/v2/messages/batch
GET  /api/chatlog/v2/group-display-name
GET  /api/chatlog/v2/media

# 无版本前缀走 CHATLOG_USE_V2 配置（默认 true→v2）
GET  /api/chatlog/health 等

# 消息搜索接口
POST /api/chatlog/messages/search   # {"group":"xxx@chatroom","query":"","senders":["wxid_xxx"],"limit":100,"offset":0,"desc":false}
# query 或 senders 至少提供一个；关键词仅匹配未压缩明文，仅按发送者时在 SQL 中按人/时间过滤

# 媒体: msg_type=3 image / 34 voice / 43 video thumb / 47 sticker / 49 file
GET  /api/chatlog/media?msg_type=&group=&server_id=&content=&hd=
# image/video-thumb 需 IMAGE_AES_KEY；voice 依赖 av/pilk，已随普通 uv sync 安装
# image hd=1 会尝试本机原图/高清 .dat（无后缀/_h/_W 等），wxgf/HEVC 会转 JPEG
# 转发聊天记录 type=19 内嵌图片目前不按普通 msg_type=3 暴露
# 文件 (49) 直接走 msg/file/<YYYY-MM>/<title> 明文, 带 Content-Disposition (UTF-8) 下载
# 返回原始 bytes + X-Media-Md5/Format + Cache-Control immutable + ETag
```

Chatlog v2 的 DB 读取接口和 `/health` 会先确认微信进程仍在运行；微信未运行或群列表读取失败时返回 `503`。`/decrypt` 是 v2 no-op，不读取数据库。

> 消息响应包含 `server_id: int` 与 `server_id_str: str` 双字段，前者保留 JSON 数字合同，后者用于 JS 客户端避免 Number 精度丢失。

## Cortex (:7999)

```
# Web UI
GET  /                                # 主页（聊天 / 任务 / Webhook 三个 tab）
GET  /manifest.json                   # PWA manifest
GET  /static/*                        # 前端静态资源

# API
GET  /api/health
GET/POST/PUT/DELETE /api/tasks        # 定时任务 CRUD
GET/POST/PUT/DELETE /api/webhooks     # Webhook 管理
GET/POST/PUT/DELETE /api/custom-commands # 本地自定义命令 CRUD
POST /api/summary                     # 群聊总结
GET  /api/groups                      # 群组列表（代理 Gateway）
POST /api/send                        # 发送消息（代理 Gateway）
GET  /api/chatlog/groups              # 群聊列表（代理 Chatlog）
GET  /api/chatlog/contacts            # 联系人（代理 Chatlog）
GET  /api/chatlog/members             # 群成员（代理 Chatlog）
GET  /api/chatlog/group-display-name  # 群聊显示名（代理 Chatlog）
GET  /api/chatlog/messages            # 查询消息（代理 Chatlog）
POST /api/chatlog/messages/search     # 搜索纯文本消息（代理 Chatlog）
POST /api/chatlog/messages/count      # 消息统计（代理 Chatlog）
POST /api/chatlog/decrypt             # 解密（代理 Chatlog）
GET  /api/chatlog/health              # 无版本健康端点 + media 能力（代理 Chatlog）
GET  /api/chatlog/media               # 富媒体 bytes 透传（代理 Chatlog）
```

## Daemon (:8001)

```
GET  /status              # 运行状态
POST /start|stop|restart  # 启停控制
POST /update              # git pull + uv sync + 重启
GET  /logs?lines=100      # 日志
```

## Prometheus Metrics

四个服务分别维护独立的内存指标，进程重启后清零：

```
GET :8000/metrics  # Gateway
GET :7998/metrics  # Chatlog
GET :7999/metrics  # Cortex
GET :8001/metrics  # Daemon
```

`/metrics` 使用与 REST API 相同的 `Authorization: Bearer <HTTP_API_TOKEN>` 认证。HTTP 请求由公共 FastAPI 中间件按 method、路由模板和 status code 统计请求量、进行中请求和延迟。业务指标使用群名以及 `result`、`client`、`message_type`、`reason`、`mode`、`model`、`source` 等有限标签，不包含 ID、时间戳、query 或消息内容。指标记录异常会被隔离，不会中断业务请求。

```yaml
scrape_configs:
  - job_name: awsl-gateway
    static_configs: [{targets: ["bot-host:8000"]}]
    authorization: {type: Bearer, credentials_file: /run/secrets/awsl-token}
  - job_name: awsl-chatlog
    static_configs: [{targets: ["bot-host:7998"]}]
    authorization: {type: Bearer, credentials_file: /run/secrets/awsl-token}
  - job_name: awsl-cortex
    static_configs: [{targets: ["bot-host:7999"]}]
    authorization: {type: Bearer, credentials_file: /run/secrets/awsl-token}
  - job_name: awsl-daemon
    static_configs: [{targets: ["bot-host:8001"]}]
    authorization: {type: Bearer, credentials_file: /run/secrets/awsl-token}
```

## WebSocket 协议

连接：`ws://host:8000/ws`

**Client → Server：**
```json
{"type": "auth", "token": "xxx"}
{"type": "subscribe", "group_filter": "regex", "message_filter": "regex", "priority": 0, "passive": false}
{"type": "unsubscribe"}
{"type": "send_message", "group_name": "群名", "message_type": "text", "message": "hello"}
{"type": "send_message", "group_name": "群名", "message_type": "image", "message": "<base64>"}
{"type": "send_message", "group_name": "群名", "message_type": "file", "message": "<base64>", "filename": "voice.mp3"}
{"type": "send_message", "group_name": "群名", "message_type": "voice", "message": "你好，这是一条语音消息"}
{"type": "send_message", "group_name": "群名", "message_type": "voice_file", "message": "<mp3-base64>", "filename": "voice.mp3"}
```

`mention_all` 是单条文本消息的可选布尔字段，默认 `false`，支持 Windows/Linux 的 `/api/send`、WS `send_message` 和主窗口 Console `/api/console/send-message`（Cortex 代理同样支持）。设为 `true` 时，先输入 `@`，直接点击新弹层第一项“所有人”或“Mention All”，再粘贴正文发送；不搜索“所有人”。账号需有群主或管理员权限，且当前微信必须提供该候选。候选缺失、不可用或目标发生变化时停止发送，不降级为普通文字。仅在正文里写 `@所有人` 或 `@all` 不会启用全体提醒。图片、文件和语音不能携带此选项，macOS 暂不支持。

```json
{"group_name":"测试群","message_type":"text","message":"请查看群公告","mention_all":true}
```

WS 请求在上述结构中增加 `"type":"send_message"`；主窗口 Console 将 `group_name` 换成 `chat_name`。该选项通过 API 或 CLI 提供，每条请求独立指定，不提供 Web UI 控件。Gateway 与 Console CLI 可使用 `--mention-all`。实际原生全体提醒须通过 Chatlog 消息的 `at_users` 包含 `notify@all` 核验，入队成功或正文显示 `@` 均不能替代核验。

`reply_to_message` 仅在 Windows/Linux 主窗口 Console 的 `/api/console/send-message` 请求结构体中提供，用于指定要回复的原始消息正文，只支持文本回复。Gateway `/api/send`、WS `send_message` 和独立窗口发送不提供引用功能；独立窗口发送前只清空正文，不检查或清理引用。
Console 不提供引用正文或只包含空白时普通发送；提供时先在当前加载的消息中从下往上精确匹配，再尝试统一空白后的匹配，每轮取最近的一条。Windows 找不到或消息不在可见区域时普通发送；Linux 则停止发送。不滚动历史，也不使用前缀、包含或相似度匹配。
Console 发送前尝试取消已有引用；Windows 清理异常只记录日志，普通发送继续，Linux 无法确认清理结果时停止发送。引用准备失败会停止本次发送；本次点过“引用”后，无论发送操作是否报错，结束时都尝试清理引用预览，结束清理失败只记录日志。按窗口和聊天名称核对，不区分同名聊天；不自动重发，清理不代表确认送达。
Gateway `/api/send` 成功及 WS `send_ok` 只表示已入队。Console `/api/console/send-message` 同步执行发送；接口结果均不能替代实际聊天记录中的引用核验。

Cortex 控制台页的文本消息（含引用回复）提供“⋯”菜单，点击后可选择“复制”或“引用”。输入区可取消引用，切换会话或消息类型时清除引用；发送失败保留草稿。普通聊天记录页不提供引用入口。

广播新增以下字段，沿用 Chatlog 已有的数据：

| 字段 | 类型 | 含义 |
|------|------|------|
| `sender` | string | 发送人的 WX ID，无法取得时为空字符串 |
| `sender_name` | string | 发送人名称，沿用 Chatlog 的名称选择规则，无法取得时为空字符串 |
| `at_users` | string[] | 被 @ 的 WX ID 列表，未取得时为空列表 |

Windows/Linux 的 Chatlog 读取方式传递这三个字段；Windows UIA、Linux AT-SPI 和 macOS OCR 未取得的信息使用上述空值，不猜测身份。

**Server → Client：**
```json
{"type": "auth_ok"}
{"type": "subscribe_ok", "group_filter": "...", "message_filter": "...", "priority": 0, "passive": false}
{"type": "message", "group_name": "群名", "content": "消息内容", "timestamp": 1710000000.0}
{"type": "send_ok"}
{"type": "error", "detail": "..."}
```

认证：WS 首条消息 `{"type": "auth", "token": "xxx"}`（10s 超时断开），REST `Authorization: Bearer <HTTP_API_TOKEN>`；`/api/chatlog/*/media` 兼容 `?token=<HTTP_API_TOKEN>`
