# SparkHub D1 聊天归档

归档模块从本机 Chatlog v2 读取指定群聊，经 HTTPS 主动上传到 SparkHub API，再写入专用 D1。支持 Windows/Linux、历史补录、持续增量、暂停/恢复、近期重扫与本地 Cortex 云端查询。不要求打开群聊独立窗口。

SparkHub admin 只接收与统计：设备心跳、积压、群进度、去重后的消息量和同步轮次。群选择、暂停/恢复和重新补录继续在本地管理。微信数据库密钥仅配置在本机。

## 1. 服务端准备

接收端统一在 [SparkHub](https://github.com/bhwa233/SparkHub) 的 `apps/api/src/wechat-archive/`；服务端配置和迁移说明见该仓库 `docs/wechat-archive.md`。机器人仓库不再维护独立的 Worker 或 Node.js 依赖。

由 SparkHub 部署归档数据库迁移和 API，配置 `WECHAT_ARCHIVE_CLIENTS` Secret，为每台设备分配独立 Token、账号和来源标识，并授权稳定群 ID。设备令牌不能读取后台统计，后台管理口令也不能代替设备上传令牌。

服务端正式和开发库独立；部署前的空库不代表接口已经上线。初次接入需同时准备 SparkHub 服务端及本仓库新版客户端。

## 2. 本地配置

先按 [平台配置](platform-setup.md) 配好 Chatlog v2 和数据库密钥，并确认能查询群和消息。归档固定访问 `/api/chatlog/v2/*`，不受 `CHATLOG_USE_V2` 默认路由选择影响。

在 `.env` 配置：

```dotenv
ARCHIVE_ENABLED=true
ARCHIVE_URL=https://api.bhwa233.com/api/wechat-archive
ARCHIVE_TOKEN=the-upload-token-for-this-device
ARCHIVE_ACCOUNT_ID=wxid_example
ARCHIVE_SOURCE_ID=desktop-windows
ARCHIVE_STATE_PATH=archive_state.db
```

同一账号跨 Windows/Linux 使用相同 `ACCOUNT_ID`，每台设备使用不同且稳定的 `SOURCE_ID`、Token 和状态文件。`ARCHIVE_URL` 支持 API 路径前缀，只接受 HTTPS，不带用户凭证、查询参数或片段。本地开发允许回环 HTTP，例如 `http://127.0.0.1:8060/api/wechat-archive`。

其他选项见 [.env.example](../.env.example)。相对状态文件路径始终以项目根目录解析，切换账号/设备须使用另一份状态文件。

```sh
uv sync --locked
uv run main.py
```

启用后 `main.py` 增加受监督的 `archive.main` 子进程。打开 Cortex「云端归档」，添加本机存在且服务端凭证已授权的群。首次添加自动补录本机现存历史，同时同步新增消息。

仅需归档时，可在独立终端分别运行：

```sh
uv run -m chatlog.main
uv run -m cortex.main
uv run -m archive.main
```

同一状态文件的归档进程使用标准库文件锁，防止重复启动后双写。没有 Gateway 时其他收发功能不可用，归档页可独立使用。

## 3. 队列与恢复

- 实时、历史和近期重扫使用独立游标，每轮每群各读一页，避免历史积压饿死增量。默认轮询 60 秒，每页 200 条，实际周期包含请求耗时。
- Chatlog seq 是闭区间，边界可能包含多条消息。归档持久化边界偏移量，跨页读取完整边界。查询失败或结果不完整时保留进度。
- 一页消息入队、内容摘要更新和游标推进处于同一 SQLite 事务。只有服务端整批提交并返回匹配的批次确认后才清除队列；断网、重启、响应丢失都可以重传。
- 旧上传确认不能删除后来更新的排队内容。有服务端 ID 时，本地队列也按正式身份区分，避免跨数据库 seq/local_id 重合而覆盖不同消息。
- 默认待上传内容上限 100 MiB，达到上限后保留旧读取位置，恢复上传后继续。此上限不包含历史身份摘要，状态文件会随归档历史增长。
- 默认每小时重扫最近 24 小时，补偿迟到记录、内容和服务端 ID 更新。更早的历史恢复、数据库重建或需要全量重发时，使用本地「重新补录」。它保留待上传内容、清除本群摘要与补录进度，云端继续去重。
- 暂停保留队列与游标，停止该群后续读取和新批次派发。已经发出的请求可能完成。继续同步后从保存位置恢复。
- 上传按 UTF-8 字节拆分，预留轮次元数据；整批最多 1,500,000 字节。单条超限会报错，保持游标，不截断或静默丢弃原文。
- 本地数据库中不存在的历史无法补录；本机删除不会自动删除云端原文，也不保证反映重扫范围外的撤回或修改。
- 归档账号需与实际微信数据库一致。状态文件能防止配置身份复用，但服务端不能自动证明本机当前登录身份。

旧归档状态文件启动时通过版本化迁移追加批次类型字段，不删除队列或既有游标。

## 4. admin 监控上报

接收端使用 SparkHub `{ success, data / error }` 信封。本地每轮独立上报设备心跳，并按最多 200 个群分批上报进度；长轮次中定期续报。报告只含设备、群和运行状态，不发送聊天正文或密钥。

消息实际上传时携带稳定批次 ID 和当前同步轮次 ID。批次标记为历史、增量、近期重扫或混合。服务端用已提交批次计算新增、更新和重复数量；重传不会重复累计，也不会因进入新一轮而重新归属。

空闲轮询只更新心跳，不生成轮次记录。轮次结束不代表所有历史已补录完毕。积压和群进度为最近上报值；断网期间 admin 显示最后报告及超时状态，不估算详细失败次数。监控上报失败不改变归档队列或确认结果。

后台入口为 SparkHub `/{locale}/admin/collector?collector=wechat`。今日新增按北京时间**入库日**计算，历史补录也计入当天；消息发送日期另列，累计数按正式消息身份去重。

## 5. 本地 API 与后续 AI

Cortex API 沿用 `HTTP_API_TOKEN` Bearer 认证，不返回云端 Token：

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | `/api/archive/status` | 本地进程心跳、队列及每群状态 |
| GET | `/api/archive/available-groups` | 本机可见与服务端授权群的交集 |
| PUT | `/api/archive/groups` | 添加、暂停或恢复同步群 |
| POST | `/api/archive/replay` | 重新补录本群历史 |
| GET | `/api/archive/messages` | 查询本设备授权账号和群的云端记录 |

文字直接保存；分享/转发提取可解析文本，同时保留 Chatlog 返回的原内容。其他类型保留内容及元数据。媒体原文件、OCR、语音转写和向量化留待后续扩展。

未来可从 D1 取相邻消息切片，保存与原消息 ID 的对应关系，再写入 Vectorize。消息原文独立于 Embedding 模型，可重建索引而无须重新采集。

## 6. 验证

```sh
uv run python -m unittest discover -s tests/archive -p 'test_*.py'
```

Python 使用现有 httpx、Pydantic、SQLiteMigrator 和标准库，没有新增依赖。SparkHub 仓库的 `apps/api/src/wechat-archive/archive.test.ts` 使用真实本地 D1 和实际迁移验证接收、去重、权限与统计。

本地回归和合成请求不能替代 Windows/Linux 实际微信读库及生产 D1 的接入验收。
