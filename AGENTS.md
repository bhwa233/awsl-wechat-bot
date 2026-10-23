# 最重要规则：所有修改必须通过 PR

**禁止直接推送或强制推送到 `main`。所有修改必须在工作分支提交，通过 Pull Request 合入 `main`。**

- 此规则适用于代码、配置、依赖、文档、脚本、测试及本文件；不能因改动小或修复紧急而跳过 PR。
- 修改前创建或切换工作分支；只推送工作分支，不绕过分支保护。
- PR 按独立功能拆分，标题、描述和提交信息使用英文；合并使用 squash。
- 未获用户授权，不自行合并 PR 或部署。用户已明确授权的操作直接继续，无需重复确认。

## 工作原则

- 只做必要的最小改动，优先早返回；避免推测性抽象、重复类型和无关重构。
- 修改前阅读相关实现，保持现有 API、WS 协议和 Base64 功能兼容。
- 验证应覆盖实际风险；不为低风险改动添加重复测试或无必要的注释。
- 配置以 `.env.example` 和 `common/config.py` 为准，依赖使用 uv 管理。
- 不提交真实 `.env`、Token、密钥、数据库、聊天内容或实机测试流水账。

## 服务与代码入口

当前维护 Windows 和 Linux；macOS 已停止维护，不建议使用。

| 服务 | 默认端口 | 职责与入口 |
| --- | --- | --- |
| Gateway | 8000 | 微信窗口交互、HTTP/WS 收发；`gateway/runtime.py`、`gateway/asgi.py` |
| Chatlog | 7998 | 独立数据库与媒体服务；`chatlog/asgi.py`、`routes.py` / `routes_v2.py` |
| Cortex | 7999 | Web UI、AI、命令、调度、总结、Webhook；`cortex/runtime.py`、`cortex/asgi.py` |
| Daemon | 8001 | Bot 启停、更新、日志与健康看护；`daemon/manager.py`、`daemon/routes.py` |

- `main.py`：监督 Gateway、Chatlog、Cortex 三个独立子进程，异常退出后重启对应服务。
- `common/`：配置、协议模型、认证、日志、指标及版本化 SQLite 迁移。
- `gateway/core/`：Detector、WS hub、串行 Sender、后台 VoiceSynthesisWorker；`SendTask` 是 `OutgoingMessage` 的别名。
- `gateway/adapters/`：平台读取/发送策略；`gateway/console/`：主窗口与独立窗口操作。
- `gateway/startup/`：微信启动、登录、窗口恢复和持续看护。
- `chatlog/platform/`、`wechat_db*.py`、`media/`：平台配置、数据库读取与富媒体处理。
- `cortex/gateway_client.py`：纯 WS 客户端；`cortex/services/`：业务服务与 ORM；`cortex/scheduler.py`：调度。
- `cortex/routes/`、`cortex/templates/`：HTTP 路由与本地 Preact / ES Modules 前端，无构建和运行时 CDN。
- `.agents/skills/`：维护仓库 skills 的唯一来源；`.claude/skills` 仅为兼容软链接。

## 修改时必须保持的约束

### 通信与线程

- Cortex 的业务消息通过 GatewayClient WS 收发；Web UI 通过 Cortex HTTP 代理访问 Gateway / Chatlog。
- Chatlog 是纯数据层，不依赖 Gateway/Cortex；数据库需位于 Chatlog 所在机器。
- MessageListener 回调运行在 GatewayClient asyncio 线程，禁止阻塞。AI/命令交给线程池，Webhook 交给有界队列与 worker。
- Sender 全局限速串行发送；TTS 在独立线程合成后回到发送队列，不能阻塞后续普通消息。
- WS 订阅保留正则过滤、`priority` 和 `passive`：被动客户端始终收到匹配消息，其他客户端只推送最高优先级。

### 载荷、认证与持久化

- 发送载荷统一为 `message_type + message + filename`，复用 `common/models.py` 校验与 `MAX_REGEX_LEN`。
- Base64 接口保持原有解码、发送与清理路径。流式文件由 API 接收、保存、计量和清理，复用发送队列及 Adapter。
- 流式上传限制为单文件 100 MiB、进程临时容量 256 MiB、5 并发、300 秒接收超时；不得整文件载入内存或转成 Base64。
- 发送入队不代表送达。文件按发送结果及 Adapter 原有延迟清理；中断或拒绝入队的文件及时释放，删除失败不能提前返还容量。
- 认证复用 `common/auth.py`，Token 比较保持常量时间。启用认证时，WS 首条消息认证并限制等待时间，Token 不放在 WS URL。
- 媒体兼容查询参数 Token；日志须脱敏，保留健康检查等高频访问日志过滤。
- HTTP 调用使用持久化 httpx 连接池；不要在异步 listener 中执行同步网络请求。
- ORM 跨字段归一化放在 Create/Update 请求模型，避免在表模型的 after validator 中修改映射属性。
- SQLite 变更使用 `common/sqlite_migrator.py` 的版本与事务机制，显式关闭连接；兼容已有数据库，迁移可重复启动。
- 消息响应保留 `server_id` 与 `server_id_str` 双字段，JS 使用字符串避免精度丢失；无法取得的发送人及 `at_users` 使用空值，不猜测身份。

### 平台交互与恢复

- Windows UIA 按线程初始化 COM/UIA；发送共用 UI 锁，Console 操作锁覆盖 TTS，合成本身不占全局 UI 锁。
- Linux 默认用 Chatlog v2 seq 游标读取，也可选 AT-SPI；微信与 Gateway 使用同一用户、X11 和对应桌面的 D-Bus。
- Linux 同 seq 边界占满一页时继续分页；任一相关数据库查询失败则保留游标重试，不接受部分结果或阻塞于自发消息。
- Linux UI 操作共用跨进程锁；AT-SPI 控件、坐标、目标会话或焦点无法确认时停止，不用截图猜测或固定坐标兜底。
- 会话列表 path 不是群 ID；稳定 ID 来自 Chatlog。同名会话无法唯一对应时拒绝自动选择和发送。
- `available=true` 不代表聊天区和输入框就绪。Linux 开独立窗口后需回选原会话；路径和缓存恢复失败须返回真实状态。
- 录音结束/取消需同时确认录音流与 UI；未确认时保留无声输入及 pending-recording 标记。完成麦克风恢复和设备清理后才删标记，不得绕过恢复保护。
- 微信启动与看护复用同一流程，不主动终止微信；遇到二维码、锁定、超时或重启限额时停止操作，保留手动窗口缓存。
- 微信与 Bot 进程隔离：Windows 通过交互式桌面的本机 WMI 启动，Linux 使用独立 systemd 用户单元；重启 Bot 不应终止微信。
- Windows/Linux 原生库来源、版本与哈希以 `chatlog/vendor/UPSTREAM.md` 为准；Linux 保持只读及 WAL 可见，不使用 `immutable`。

## 常用命令

```bash
uv sync
cp .env.example .env
uv run main.py              # 三个服务及子进程监督
uv run run_daemon.py        # Daemon 远程管理入口
uv run -m gateway.main     # 单独运行 Gateway
uv run -m chatlog.main     # 单独运行 Chatlog
uv run -m cortex.main      # 单独运行 Cortex
uv add <package>           # 更新 pyproject.toml / uv.lock
uv remove <package>
uv lock
```

## 验证与实机操作

- 标准库 unittest 按服务运行，仅执行与改动相关的检查；前端和完整测试命令见 [测试说明](tests/README.md)。
- 文档改动检查本地链接、锚点和 Mermaid 语法；提交前运行 `git diff --check`。
- 实机操作优先使用 `.agents/skills/` 下的 Daemon、Gateway、Console 和 Chatlog skill，并遵守用户指定的机器、群和消息类型。
- Console UI/API 返回不能代替送达。通过 Chatlog 匹配自发消息；附件核对文件内容，语音验证可解码。
- 未查到消息时至少轮询 120 秒，再做最后一次新查询，之后才能判失败或重试，禁止盲目重发。

## 详细文档

- [项目与总架构](README.md)
- [配置、运行与触发方式](docs/configuration.md)
- [HTTP、WebSocket 与监控](docs/api.md)
- [线程模型](docs/architecture.md)
- [平台配置与疑难排查](docs/platform-setup.md)
- [微信启动与窗口恢复](docs/wechat-startup.md)
- [操作系统自启部署](docs/boot-autostart.md)
- [数据库密钥提取](docs/wechat-db-key-extraction.md)
- [原生库来源](chatlog/vendor/UPSTREAM.md)
