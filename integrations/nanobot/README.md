# NanoBot 接入 AWSL Gateway

NanoBot (Python) 作为 WS 客户端连接 Gateway，处理 AI 对话。

## 安装

```bash
cd integrations/nanobot
uv sync
```

## 配置

编辑 `config.yaml`：
- `llm` — LLM 提供商和模型配置
- `channels.awsl-gateway.ws_url` — Gateway WS 地址（含 token）
- `channels.awsl-gateway.group_filter` — 群名正则过滤（空=所有群）
- `channels.awsl-gateway.message_filter` — 消息正则过滤
- `agent.system_prompt` — AI 系统提示词

## 运行

```bash
cd integrations/nanobot
uv run main.py
```

## 与 Cortex 共存

NanoBot 和 Cortex 作为独立 WS 客户端并行运行：
- NanoBot 处理 AI 对话（通过 `message_filter` 分流）
- Cortex 保留命令、定时任务、Webhook 功能
- 如需 NanoBot 完全替代 Cortex AI，可将 `TRIGGER_KEYWORD` 设为空
