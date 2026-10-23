# 测试

Python 回归使用标准库 unittest，按服务放在 `tests/cortex/`、`tests/gateway/`、`tests/chatlog/`、`tests/runtime/`。使用临时数据库、模拟 API 和平台控件 Mock，不操作真实微信。

## Python 回归

在项目根目录运行：

```sh
uv run python -m unittest discover -s tests/cortex -p "test_*.py"
uv run python -m unittest discover -s tests/gateway -p "test_*.py"
uv run python -m unittest discover -s tests/chatlog -p "test_*.py"
uv run python -m unittest discover -s tests/runtime -p "test_*.py"
```

Cortex 覆盖 ORM、迁移、接口和静态资源；Gateway 覆盖消息读取、发送、窗口操作与启动看护；Chatlog 覆盖查询和分页；runtime 覆盖子进程监督。平台相关用例按环境跳过，以实际运行结果为准。

## 前端回归

```sh
npm install --prefix /tmp/awsl-preact-qa playwright
PLAYWRIGHT_MODULE=/tmp/awsl-preact-qa/node_modules/playwright/index.mjs node tests/cortex/frontend/run.mjs
```

默认使用本机 Chrome，可通过 `PLAYWRIGHT_CHANNEL` 配置；命令末尾追加 `chat` 可只检查聊天页。详细说明见 [Cortex 前端](../cortex/templates/README.md)。

查看 Mock 页面只需 Node.js，无需启动业务服务：

```sh
node tests/cortex/frontend/preview.mjs
```

打开 http://127.0.0.1:7997/。修改只保存在内存中，重启恢复；通过 `MOCK_PORT` 更换端口。

## 实机验证

实机发送必须获得授权并限定目标群。通过 Chatlog 自消息确认送达；附件应核对文件内容，语音应验证可解码。未查到记录时至少轮询 120 秒并做最后一次新查询，再判失败或重试。UI/API 返回不能替代送达确认。

原生库只读与 WAL 回归由上游 release pipeline 运行，来源和哈希见 [原生库来源](../chatlog/vendor/UPSTREAM.md)。
