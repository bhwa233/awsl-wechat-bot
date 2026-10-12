# 微信 Bot 项目接手文档

> 2026-09-23 最后更新

## 项目概述

微信群聊机器人基础设施：消息同步 + 媒体归档 + 行情查询 + 撤回保留 + 自动愈合。部署在 Debian VM 上，通过 X11 与 Linux WeChat 4.x 交互。

## 仓库

| 仓库 | 地址 | 说明 |
|---|---|---|
| **wx-sync-rs** | `github.com/solarhell/wx-sync-rs` (私有) | Rust Bot 核心 |
| **quotes** | `github.com/solarhell/quotes` (私有) | Python 行情服务 |
| **awsl-wechat-bot** | `github.com/awsl-project/awsl-wechat-bot` (上游) | Python 原版参考 |

## 架构

```
微信 Linux 4.x (SQLCipher 加密 SQLite)
  │
  ├─ wx-syncd (Rust, :8001)
  │   ├── 消息同步 → PostgreSQL (活库直读 ro+nolock)
  │   ├── 媒体归档 → Cloudflare R2 (七类: 图/视频/语音/表情×3/文件/头像)
  │   ├── 撤回保留 (标记不删 + 竞速抢文件)
  │   ├── X11 发送 (文本/图片/文件)
  │   ├── Watchdog (自动登录 + 群窗管理 + DB自动愈合)
  │   └── 命令钩子 → quotes 服务
  │
  ├── quotes (Python, :8300)
  │   ├── A股/港股/美股 (东方财富→腾讯→雪球 三源搜索)
  │   ├── 加密货币 (Gate.io→Binance)
  │   └── 别名热加载 (/电动爹 /m7 /中概 /房地产)
  │
  └── PostgreSQL ← 外部 Agent / AI
```

## VM 环境

| 项 | 值 |
|---|---|
| VM | Debian 12, XFCE 桌面, 6GB RAM |
| 微信 | Linux WeChat 4.1.13 (/opt/wechat/wechat) |
| Rust | stable via rustup |
| DB | PostgreSQL 14+ (远程 RDS 或本地) |
| 存储 | Cloudflare R2 (S3 兼容) |
| 通知 | Bark (手机推送) |
| systemd | wx-syncd + quotes 两个服务 |

## 配置文件分层

| 文件 | 权限 | 用途 | 变更频率 |
|---|---|---|---|
| `wx-sync/config.toml` | 644 | PG DSN / R2 / Bark / 行为参数 | 手动 |
| `wx-sync/passphrase.hex` | 600 | 微信主密钥 (32B) | 永不变 |
| `wx-sync/keys.json` | 600 | 20 把 DB 密钥 | DB 重建时自动 |
| `quotes/config.toml` | 644 | 端口 / 代理 / 超时 | 手动 |
| `quotes/aliases.json` | 644 | 股票别名 (热加载) | 随时改 |

## 密钥管理

```
passphrase (微信主密钥, 每次登录一致)
  ↓ PBKDF2-SHA512(passphrase, salt, 256000)
keys.json (per-DB 密钥, salt 来自各 DB 首页)
```

- **首次**: `wx-sync keys capture config.toml` (需点登录按钮)
- **后续**: 同命令秒级完成 (passphrase.hex 快速路径)
- **自动**: DB 自愈时自动调用

## 部署

```bash
# 编译 wx-sync
cd wx-sync && cargo build --release

# 安装依赖
pip3 install --user curl-cffi

# 安装 systemd 服务
sudo cp deploy/*.service /etc/systemd/system/
# 编辑 service 文件替换 YOUR_USER
sudo systemctl daemon-reload

# 初始化
wx-sync/target/release/wx-sync apply-schema wx-sync/config.toml

# 启动
sudo systemctl start quotes wx-syncd
```

## 群内命令

| 命令 | 功能 |
|---|---|
| `/tsla` `/qqq` `/ndx` | 美股/指数 |
| `/00700` `/sh600519` | 港股/A股代码 |
| `/腾讯` `/万科` `/宁德时代` | 中文名搜索 |
| `/电动爹` | EV: 宁德+比亚迪+特斯拉+小米+理想+蔚来+小鹏+赛力斯 |
| `/m7` | 美股七巨头 |
| `/中概` | 腾讯+阿里+拼多多+小米+比亚迪+美团+京东+百度+快手 |
| `/房地产` | 张江高科+保利+万通+招商蛇口+万科+滨江+新城+南京高科等 |
| `/btc` `/eth` | 加密货币 |

## 核心能力（已全部实战验证）

### 消息同步
- 活库直读 (SQLCipher ro+nolock, 零拷贝零锁)
- 撤回实时标记 (4.1.13: 原地改写+精确 seq 关联, 不删原文)
- 引用回复解析 (reply_to_server_id)
- 转发聊天记录 (嵌套自引用, 内容寻址去重)

### 媒体归档
- 七类全覆盖: 图片(V2) / 视频 / 语音(SILK→MP3) / 系统表情 / 自定义表情 / 表情包切片 / 文件 / 头像
- 即时上传 (事件驱动, 撤回竞速: 0s hardlink 冻结 → 重试阶梯 → R2)
- 变更历史 (头像/昵称/群内昵称/群成员进出)

### DB 自动愈合 (生态首个)
- 断流检测 (lag >30min) → 实读体检 → 确认损坏
- 备份坏库 → 杀微信 → watchdog 拉起+自动登录 → DB 重建
- passphrase 秒派生 → 同步恢复 → Bark 通知
- 安全: 发送锁保护 outbox / 预算1次每小时 / 连续失败3次停止

### Watchdog (无人值守全链)
- 微信死了 → 拉起 (带 AT-SPI + XDG 环境变量)
- 登录按钮 → mousedown/mouseup 自动点击 (Qt 兼容)
- 扫码界面 → 解码 + Bark 推二维码
- 群窗丢失 → AT-SPI 语义定位补开
- daemon 启动时并行跑 watchdog (不傻等登录)

## 运维

### 健康检查
```bash
wx-sync/target/release/wx-sync status wx-sync/config.toml
systemctl status wx-syncd quotes
journalctl -u wx-syncd -f
```

### DB 重建 (手动, 自动愈合失败时)
```bash
sudo systemctl stop wx-syncd
pkill -x wechat
mv ~/.xwechat/.../message_0.db* ~/db_backup/
wx-sync/target/release/wx-sync keys capture wx-sync/config.toml
sudo systemctl start wx-syncd
```

### 发送止血 (群消息异常时)
```bash
psql ... "UPDATE outbox SET status='dead' WHERE status IN ('pending','sending')"
sudo systemctl stop wx-syncd
```

## 已知限制

| 项 | 说明 |
|---|---|
| 扫码需真人 | 微信登录会话过期后必须手机扫码 (物理限制) |
| 秒撤不可恢复 | CDN 下载完成前撤回 = 文件从未落盘 |
| 语音发送 | Linux 不支持 (仅接收) |
| R2 密钥已暴露 | `src/bin/r2test.rs` 曾推到 GitHub, 需轮换 |

## 待做 (优先级排序)

1. **AI Agent 层** — nanobot 工具面参考已克隆 (`contrib/awsl-nanobot-wechat`)
2. **群聊总结** — 消息数据完整, 缺 LLM 调用 + 渲染 + 发送
3. **Webhook 转发** — 消息实时同步已有, 缺 HTTP POST fire-and-forget
4. **定时任务** — outbox 表已定义, 缺 cron 调度器

## 关键教训 (踩过的坑)

1. **不要频繁杀微信** — 每次都可能换 salt 重加密 DB + 累积 X11 脏状态
2. **systemd 需要 XDG_RUNTIME_DIR** — 缺了会导致 AT-SPI 不注册 + 微信崩溃
3. **Qt 按钮不接受合成 click** — 必须 mousedown/delay/mouseup 分离
4. **文件发送走 text/uri-list** — 内容 MIME 粘贴只产生空气泡
5. **4.1.13 撤回 = 原地改写** — 不是删行+插新行, sysmsg 的 seq 即原消息位置
6. **WAL 模式下主文件旧页是正常的** — HMAC 逐页验大量误报, 用实读验证
7. **pkill -f 会自杀** — 模式匹配到自身命令行, 用 `sen[d]-test` 括号绕开
8. **capture 派生太早会漏 msg0** — 需等核心库落盘再 derive_all

## VM 访问

```
ssh wechat-vm          # 三路由别名 (Tailscale/LAN/PVE)
# 或
ssh jiaxin@100.95.211.4  # Tailscale
```

密码: 见 .env 或联系管理员
