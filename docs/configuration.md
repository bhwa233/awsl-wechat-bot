# 配置、运行与触发方式

[返回 README](../README.md)

以下命令均在项目根目录执行。

## 基础配置 (.env)

```env
# 触发关键词
TRIGGER_KEYWORD=awsl

# 机器人名称列表（@触发，JSON 数组格式）
BOT_NAMES=["机器人","小助手"]

# OpenAI API
OPENAI_API_KEY=your-api-key-here
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o-mini

# 群聊总结图片渲染：填写浏览器可执行文件的完整路径
BROWSER_EXECUTABLE=

# 远程命令（不配置 COMMAND_API_BASE_URL 等同关闭远程命令）
REMOTE_COMMANDS_ENABLED=true
COMMAND_API_BASE_URL=

# 服务端口
HTTP_API_PORT=8000
HTTP_API_TOKEN=your-secret-token  # 留空不启用认证
WS_MAX_SIZE=67108864
CORTEX_HTTP_PORT=7999
CHATLOG_HTTP_PORT=7998

# Chatlog 默认后端（true→v2 原生库，false→v1 pycryptodome）
CHATLOG_USE_V2=true

# Linux Gateway 通过 X11 发送，默认通过 Chatlog 读取消息
LINUX_WECHAT_DISPLAY=:10
LINUX_WECHAT_XAUTHORITY=~/.Xauthority
LINUX_CHATLOG_MESSAGE_LIMIT=20
LINUX_CHATLOG_GROUP_CACHE_TTL=30
```

远程命令服务需提供 `GET /command/list` 和 `GET /command?command=...`；Cortex 使用查询参数传递完整命令。

## 运行

```bash
# 一体启动（Gateway + Chatlog + Cortex）
uv run main.py

# 独立启动
uv run -m gateway.main     # Gateway only (:8000)
uv run -m chatlog.main     # Chatlog only (:7998)
uv run -m cortex.main      # Cortex only (:7999)
uv run run_daemon.py       # Daemon (:8001)
```

一体启动时，Gateway、Chatlog、Cortex 分别在独立子进程运行，共用进程监督逻辑；任意服务异常退出后 1 秒重启该服务，其他服务保持运行。停止时统一关闭并回收子进程，5 秒内未退出则强制结束。现有接口和健康检查保持不变。整机内存耗尽仍需部署侧资源限制；单独启动服务时由外部进程管理器负责重启。

## 触发方式

| 触发方式 | 示例 | 说明 |
|---------|------|------|
| 关键词+问题 | `awsl 今天天气怎么样？` | 触发 AI 回复 |
| @机器人 | `@机器人 帮我写首诗` | 触发 AI 回复 |
| 刷新命令 | `awsl refresh` / `awsl 刷新命令` | 刷新远程命令列表（未配置远程命令或当前群受远程命令白名单限制时禁用） |
| 动态命令 | `<命令名> <参数>` | 执行远程命令（未配置远程命令时禁用；白名单策略文件不存在或白名单为空时不限制） |

远程命令白名单可在 Web UI 的「命令」页顶部「远程命令」卡片中编辑，命令选项会从远程命令服务自动加载。策略存放在 `~/.awsl-wechat-bot/remote_command_policy.json`，文件不存在或 `allowlist` 为空表示允许全部远程命令：

```json
{
  "allowlist": ["weather", "roll"],
  "bypass_groups": ["管理群", "测试群"]
}
```
