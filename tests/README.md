# 测试

按服务模块组织：`tests/cortex/`、`tests/gateway/`、`tests/chatlog/`、`tests/daemon/`。仅在对应服务有测试时创建目录。

当前 `cortex/` 包含任务接口、Web UI 静态资源和前端浏览器回归；所有数据使用临时数据库或模拟 API，不操作真实微信。
`gateway/` 包含控制台列表解析、Linux seq 游标/同 seq 跨页/边界去重、未确认自消息不阻塞积压、输入焦点保护、原生 @、引用正文核对、同名会话拒绝、开窗后主窗口恢复和 WebSocket 正则校验。Linux 用例使用 Mock，不连接真实微信；可在 macOS/Linux 运行，Windows 跳过 POSIX 用例。`chatlog/` 用临时 SQLite 验证批量查询跨库合并后的分页和旧请求兼容性。

Linux 实机验证需在授权测试群逐条发送，并通过 Chatlog 自消息确认文本、图片、文件、引用和语音；引用核对原 server ID，原生 @ 核对 `at_users`。附件和语音查询使用 `text_only=false`，UI/API 返回不能替代送达。未查到记录时至少轮询 120 秒并做最后一次新查询，再判失败或重试。另测窗口开关/缓存恢复与焦点抢占拦截。原生库 WAL 读取/只读拒写回归已包含在上游 v0.1.22 的 `cmd/native/readonly_linux_test.go` 中，由 release pipeline 运行；CI 产物来源和哈希见 `chatlog/vendor/UPSTREAM.md`。

2026-10-22：Debian 13 x86_64 / WeChat 4.1.13.9 的主窗口与独立窗口已分别完成发送验收；完整矩阵、故障原因与验证边界见 [Linux Gateway 实机验收](../docs/linux-gateway-acceptance.md)。Gateway 53 项、Chatlog 3 项、Cortex 4 项、服务监督器 3 项回归通过；新增覆盖 TTS 锁范围、部分读库失败、取消录音失败及恢复、窗口恢复失败结果。 随后新增 3 项临时附件清理回归（图片/文件的录音保护异常、返回失败及成功路径），该轮 Gateway 56 项通过；Linux 机器另验证真实后台线程删除临时文件及目录。该记录不代表 ARM64、其他微信版本或其他语言界面已完成同等验证。

2026-10-23（Asia/Singapore）：补充麦克风清理失败恢复、长文件名字节限制和截断防撞名、部分写入清理、窗口缓存保护、中英文引用、时间格式正文区分、置顶预览和 AT-SPI 调试能力回归。Gateway 当前 69 项，连同 Chatlog/Cortex/服务监督器共 79 项 Python 回归通过，前端浏览器 34 项检查通过。最终两种窗口文件下载 SHA256、语音及单次清理失败恢复均由实机 Chatlog 确认；早期微信客户端发送队列异常与恢复过程在验收文档中单独记录。

```sh
uv run python -m unittest discover -s tests/cortex -p "test_*.py"
uv run python -m unittest discover -s tests/gateway -p "test_*.py"
uv run python -m unittest discover -s tests/chatlog -p "test_*.py"

npm install --prefix /tmp/awsl-preact-qa playwright
PLAYWRIGHT_MODULE=/tmp/awsl-preact-qa/node_modules/playwright/index.mjs node tests/cortex/frontend/run.mjs
```

浏览器检查默认使用本机 Chrome；可通过 `PLAYWRIGHT_CHANNEL` 配置。单独检查聊天页面可在命令末尾追加 `chat`。详细说明见 [Cortex 前端](../cortex/templates/README.md)。

手动查看 Mock 页面（只需 Node.js，不需要启动业务服务或安装 Playwright）：

```sh
node tests/cortex/frontend/preview.mjs
```

打开 http://127.0.0.1:7997/。使用相同的测试模拟数据，支持 CRUD 和模拟控制台操作，修改仅保存在内存中，重启恢复。通过 `MOCK_PORT` 可更换端口。
