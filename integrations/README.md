# Integrations - 外部 AI Agent 接入 Gateway

AWSL Gateway 作为 WS 消息网关，支持任意 WS 客户端接入。本目录包含两种 AI Agent 框架的集成：

## 架构

```
Detector (n) -> Gateway WS Server (:8000)
                       | 广播
          +------------+------------+
          v            v            v
    GatewayClient   NanoBot      外部 WS
    (Cortex :7999)  (AI Agent)   客户端
    命令/定时/Webhook  AI 对话+MCP
```

## NanoBot (推荐)

Python 同栈、轻量、BaseChannel 简洁。详见 [nanobot/README.md](nanobot/README.md)。

```bash
pip install nanobot-ai websockets
cd integrations/nanobot
python run.py
```

## OpenClaw (可选)

Node.js 生态，ClawHub Skills、多模型路由。详见 [openclaw/README.md](openclaw/README.md)。

```bash
cd integrations/openclaw
npm install
openclaw --config openclaw.json
```

## 与 Cortex 共存

两者作为独立 WS 客户端，和 Cortex 完全并行，互不干扰。可通过 `message_filter` 正则分流消息：
- NanoBot 订阅 AI 对话（如 `^@bot`）
- Cortex 保留命令/定时任务/Webhook
