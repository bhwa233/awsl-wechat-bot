# 测试

按服务模块组织：`tests/cortex/`、`tests/gateway/`、`tests/chatlog/`、`tests/daemon/`。仅在对应服务有测试时创建目录。

当前 `cortex/` 包含任务接口、Web UI 静态资源和前端浏览器回归；所有数据使用临时数据库或模拟 API，不操作真实微信。
`gateway/` 包含控制台列表解析测试，可在非 Windows 环境运行，不加载 UIAutomation。

```sh
uv run python -m unittest discover -s tests/cortex -p "test_*.py"
uv run python -m unittest discover -s tests/gateway -p "test_*.py"

npm install --prefix /tmp/awsl-preact-qa playwright
PLAYWRIGHT_MODULE=/tmp/awsl-preact-qa/node_modules/playwright/index.mjs node tests/cortex/frontend/run.mjs
```

浏览器检查默认使用本机 Chrome；可通过 `PLAYWRIGHT_CHANNEL` 配置。单独检查聊天页面可在命令末尾追加 `chat`。详细说明见 [Cortex 前端](../cortex/templates/README.md)。

手动查看 Mock 页面（只需 Node.js，不需要启动业务服务或安装 Playwright）：

```sh
node tests/cortex/frontend/preview.mjs
```

打开 http://127.0.0.1:7997/。使用相同的测试模拟数据，支持 CRUD 和模拟控制台操作，修改仅保存在内存中，重启恢复。通过 `MOCK_PORT` 可更换端口。
