# Cortex 前端

Preact 10.29.8 + 原生 ES Modules，以 `h()` 编写组件。无 JSX、TypeScript、模板库或生产构建步骤。后端照常通过 FastAPI `/static/*` 提供文件，入口为 `index.html` → `app.js`。

Preact 及同包 Hooks 已固定在 `vendor/preact/`，由 `index.html` 的本地 Import Map 统一解析 `preact` / `preact/hooks`，运行时不访问 CDN，许可证和来源见该目录。升级时保持核心和 Hooks 版本一致，并更新 Import Map 的版本号；保留上游模块的导入声明。

依赖加载遵循 [Preact 无构建指南](https://preactjs.com/guide/v10/no-build-workflows/)。继续使用 `h()` 和同包 Hooks，实现轻量响应式，不增加模板库或状态库。

## 文件职责

- `app.js`：应用上下文、导航与浏览器历史、主题、Token、概览。
- `api.js`：鉴权、HTTP 错误、超时和取消；公共数据格式化。
- `ui.js`：基础控件、焦点管理弹窗、请求与可见性轮询 Hooks。
- `resources.js`：任务/命令/Webhook 共用的编辑、提交、删除状态。
- `message_composer.js`：受控输入、消息类型、附件读取/拖拽/粘贴和载荷校验。
- `chatlog.js`：会话、消息分页、取消过期请求、联系人和群成员缓存。
- `messages.js`：共享消息与富媒体组件、稳定消息 key、成员/转发记录/原图弹窗。
- `chat.js`、`console.js`：聊天与 Windows/Linux 控制台页面（UIA/AT-SPI）。
- `tasks.js`、`custom_commands.js`、`webhooks.js`：管理页面。

Preact 管理 DOM；仅在焦点、滚动和媒体交互中使用 DOM 引用。接口返回的文本由 Preact 转义，不拼接动态 HTML 或内联事件。更换 Token 会重建页面，清理旧会话状态；消息轮询仅在页面激活且浏览器可见时进行。

服务端数据由 `useResource` 统一持有，编辑草稿独立保存；写入响应通过 `update()` 更新同一份数据并取消旧读取，重新进入页面会重新读取。聊天发送目标默认从当前会话与在线群列表推导，仅用户手动选择时保存覆盖值，切换会话后恢复自动匹配。

远程策略在读取期间或读取失败时禁止开始编辑，避免从旧数据生成草稿。控制台后台轮询保留输入框焦点与内容，仅暂缓发送；发送与窗口操作仍串行执行。Webhook 群聊和消息过滤正则按原文保存，保留有匹配含义的首尾空白。

消息容器恢复可见或尺寸变化时，仅在用户仍跟随最新消息的情况下滚动到底部，保留主动浏览历史的位置。管理页面的读取和写入未完成时禁止新建，避免旧请求覆盖另一份表单。

手动预览：运行 `node tests/cortex/frontend/preview.mjs`，打开 http://127.0.0.1:7997/。此入口复用测试的 Mock API，独立于业务服务，数据仅保存在内存中。

## 验证

后端静态资源与真实 SQLite 接口检查：

```sh
uv run python -m unittest discover -s tests/cortex -p "test_*.py"
```

浏览器回归使用 Playwright 和本机 Chrome，仅访问测试进程的本地静态服务器，所有业务 API 都被模拟，包含发送/窗口操作，不连接真实微信或业务数据库。Node.js 仅用于此开发检查，生产部署不需要。

```sh
npm install --prefix /tmp/awsl-preact-qa playwright
PLAYWRIGHT_MODULE=/tmp/awsl-preact-qa/node_modules/playwright/index.mjs node tests/cortex/frontend/run.mjs
```

可用 `PLAYWRIGHT_CHANNEL` 指定浏览器通道，`FRONTEND_TEST_ARTIFACTS` 指定截图目录。默认截图写入系统临时目录。测试覆盖本地 Import Map 与单一 Preact 实例、表单 CRUD、失败重试、文件附件、总结配置、策略重新读取与过期响应取消、慢速 Gateway 下的会话恢复与手动发送目标、会话分页与请求竞态、媒体节点保留、输入法与重复发送保护、控制台、移动端、主题和认证。

测试按服务归类在 `tests/cortex/`；浏览器用例可在入口后指定 `app`、`tasks`、`webhooks`、`commands`、`chat` 或 `console` 单独运行。每个浏览器模块使用独立的上下文与模拟数据。
