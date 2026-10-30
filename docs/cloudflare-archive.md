# Cloudflare D1 聊天归档

归档模块从本机 Chatlog v2 读取指定群聊，经 HTTPS 主动上传到 Worker，再写入 D1。支持 Windows/Linux、历史补录、持续增量、暂停/恢复、近期重扫与云端查询。它不依赖 Gateway 的实时广播，也不要求打开群聊独立窗口。

第一期保存各类消息的内容及元数据，不上传媒体原文件，不创建向量索引，不调用 AI。文字和可解析的分享/转发内容另存为可检索文本，原内容完整保留，便于后续切片、OCR/转写和向量化。

## 1. 部署云端

Worker 是独立开发工具目录 `cloudflare/archive-worker`，不改变 Python 项目的 uv 管理方式。需要 Node.js 22+。唯一新增开发依赖是 Cloudflare 官方 Wrangler（MIT OR Apache-2.0），用于构建、本地 D1 和部署；Worker 没有运行时第三方依赖。

在该目录执行：

```sh
npm ci
npx wrangler login
npx wrangler whoami
npx wrangler d1 create awsl-chat-archive
```

将返回的数据库 ID 写入 `wrangler.jsonc` 的 `database_id`，替换全零占位值。

设置 `ARCHIVE_CLIENTS` Secret，值为 JSON 数组，每台设备一项：

```json
[
  {
    "token": "replace-with-a-random-token-at-least-24-characters",
    "account_id": "wxid_example",
    "source_id": "desktop-windows",
    "groups": ["example@chatroom"]
  }
]
```

```sh
npx wrangler secret put ARCHIVE_CLIENTS
npx wrangler secret put ARCHIVE_READ_TOKEN
npm run migrate:remote
npm run deploy
```

`ARCHIVE_READ_TOKEN` 是独立的只读管理凭证，可跨设备查询；不允许上传。设备凭证只能上传和查询绑定账号的已授权群。不要把 Cloudflare API 管理令牌配置给机器人。增加可同步群时先更新云端 Secret，再在本地管理页添加。

同一账号在 Windows/Linux 使用相同 `account_id`、不同 `source_id` 和不同 Token。Secret 不提交到 Git。新增授权项时保留其他现有项。

## 2. 配置并启动机器人

先按 [平台配置](platform-setup.md) 配好 Chatlog v2 和数据库密钥，并确认能查询群和消息。归档固定访问 `/api/chatlog/v2/*`，不受聊天页 `CHATLOG_USE_V2` 默认路由选择影响。

在 `.env` 配置：

```dotenv
ARCHIVE_ENABLED=true
ARCHIVE_URL=https://your-archive-worker.workers.dev
ARCHIVE_TOKEN=the-upload-token-for-this-device
ARCHIVE_ACCOUNT_ID=wxid_example
ARCHIVE_SOURCE_ID=desktop-windows
ARCHIVE_STATE_PATH=archive_state.db
```

`ARCHIVE_URL` 只接受 HTTPS 源站地址，不含路径、查询参数或用户凭证。本地开发允许回环 HTTP。其他参数见 [.env.example](../.env.example)。

```sh
uv run main.py
```

启用后增加一个受监督的 `archive.main` 子进程，异常退出后独立重启。打开 Cortex 的「云端归档」页，添加本机存在且云端已授权的群。列表会区分同名群的稳定 ID。首次添加自动补录全部本机可见历史，同时同步新增消息。

仅需归档时可分别运行 Chatlog、Cortex 和归档进程，无须运行 Gateway：

```sh
uv run -m chatlog.main
uv run -m cortex.main
uv run -m archive.main
```

每条命令在独立终端运行。不要同时通过 `main.py` 和独立命令启动同一归档任务；进程会锁定本地状态文件，防止双写。Cortex 的其他依赖 Gateway 功能此时不可用，不影响归档页。

## 3. 同步与恢复语义

- 本地状态文件绑定账号和来源设备，不允许改配置后复用到另一账号/设备。相对路径始终以项目根目录解析。若微信配置的数据库换了账号，必须同时调整归档账号、凭证和状态文件；归档无法自动证明数据库当前账号身份。
- 首次读取记录历史交接位置。实时、历史、近期重扫使用独立游标，每轮每群各读一页，避免大群历史阻塞其他群。默认每轮间隔 60 秒，每页 200 条，串行请求限制本机读库压力；群数很多时实际周期还包含请求耗时。
- Chatlog 的 seq 是闭区间，边界可能有多条消息。归档保存该 seq 已消费的偏移量，跨页读取全部边界记录。请求失败、结果不完整或消息校验失败时保留原进度。
- 一页消息入队、内容摘要更新和游标推进在同一个 SQLite 事务内完成。断网后队列保留并指数退避重试；云端整批提交并返回对应批次确认后才删除队列。响应丢失可安全重传。
- 默认待上传内容上限 100 MiB。达到上限会停止推进相应页的读取游标，恢复上传后继续；它不是状态文件总磁盘上限。状态文件还保存历史消息的身份与摘要，用于避免周期重扫重复上传。
- 默认每小时重扫最近 24 小时，以补偿迟到消息、内容及服务端 ID 更新。更早的历史恢复、数据库重建、云端数据恢复或需要全量重发时，用「重新补录」。它保留队列、清除本群本地摘要和补录游标，重新扫描本机现存记录，云端去重。
- 暂停保留游标与队列，停止该群后续读取和新批次派发；已经发出的请求可能完成。继续同步后从保存位置恢复。云端已有记录保留，仍可查询。
- 单条/整批 UTF-8 JSON 上传上限为 1,500,000 字节；批次自动按字节拆分。单条超限会报错且不跳过消息。当前版本需人工处理该记录或后续扩展 R2 大文本存储，不会截断原文。
- 微信进程、密钥和数据库必须可用；历史仅包含本机实际保存的记录。云端归档不会因本机删除而自动删除，也不保证反映重扫范围外的撤回/修改。

## 4. 消息身份及后续 AI

D1 的 `archive_messages` 保存正文和来源字段；`archive_observations` 保存设备上的消息身份与 seq/local_id；`archive_groups` 保存账号内的群名快照。日期存 Unix 秒，观察时间存 Unix 毫秒。64 位 `server_id` 存字符串，HTTP 不经过 JavaScript Number。

有有效服务端 ID 时按账号、群和服务端 ID 归一，跨设备同步同一消息只保留一条正文。缺失时按设备及本地身份归档，不按内容去重。后续从同一观察取得服务端 ID 时关联到正式消息。服务端 ID 一直缺失或本地身份因数据库重建而改变的记录，无法可靠跨设备合并，保留为独立消息。

未来可从 D1 选取相邻消息切片，记录片段与原消息 ID 的映射，再生成中文 Embedding 写入 Vectorize。查询时先按账号/群/时间限定范围，再混合关键词与向量召回，回查 D1 上下文，最后交给 AI 并附来源。模型、切片和索引应保留版本，原文不因换模型而重采集。

## 5. 查询与状态 API

Cortex 接口沿用 `HTTP_API_TOKEN` Bearer 认证，不返回云端 Token。完整消息通过 Preact 文本节点显示，不作为 HTML 执行。

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/archive/status` | 进程心跳、待上传数量/字节、每群补录和错误状态 |
| GET | `/api/archive/available-groups` | 本机可见与云端授权群的交集 |
| PUT | `/api/archive/groups` | `{ "talker": "example@chatroom", "enabled": true }` 添加/暂停/恢复 |
| POST | `/api/archive/replay` | `{ "talker": "example@chatroom" }` 重新补录 |
| GET | `/api/archive/messages` | 按群、正文关键词、发送人和时间查询云端记录 |

Worker 提供 `/v1/scope`（当前设备授权范围）、`POST /v1/batches`、`GET /v1/messages` 和 `GET /v1/groups`。后三个查询路径中，消息和群列表需传 `account_id`；消息查询还必须传 `talker`。消息按时间倒序，以 `next_cursor` 取更早页。可选参数：`q`、`sender`、`start`/`end`（Unix 秒，闭区间）、`limit`（1–200）、`cursor`。关键词为字面量子串，限定群/时间使用索引；首期不提供全文分词或语义搜索。

## 6. 本地验证和容量

Python 复用标准库 unittest、现有 httpx 和 SQLite 迁移器，没有新增 Python 依赖。Worker 测试使用临时目录中的本地 workerd/D1，运行真实迁移与 SQL；不会连接远程数据库。

```sh
uv run python -m unittest discover -s tests/archive -p 'test_*.py'
cd cloudflare/archive-worker
npm ci
npm run check
npm test
```

手动开发时复制 `.dev.vars.example` 为 `.dev.vars`，填测试身份，执行 `npm run migrate:local` 和 `npm run dev`。`.dev.vars`、本地数据库和 `.wrangler` 均已忽略。

每天 2000 条约为每年 73 万条；假设单条正文及元数据平均 2 KB，约 1.5 GB/年，尚未计算索引和设备观察表。D1 免费单库 500 MB、付费单库 10 GB，长期保存应测量实际样本及索引体积。将来接入 Vectorize、Embedding 和 R2 会有各自用量。

参考：[D1 限制](https://developers.cloudflare.com/d1/platform/limits/)、[D1 计费](https://developers.cloudflare.com/d1/platform/pricing/)、[Vectorize](https://developers.cloudflare.com/vectorize/)。本地测试不能替代 Windows/Linux 实机读库和部署后的生产 D1 验收。
