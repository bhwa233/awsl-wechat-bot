# OpenClaw 接入 AWSL Gateway

OpenClaw (Node.js) 作为 WS 客户端连接 Gateway，利用 ClawHub Skills 生态。

## 安装

```bash
cd integrations/openclaw
npm install
```

## 配置

编辑 `openclaw.json`：
- `awsl-gateway.wsUrl` — Gateway WS 地址（含 token）
- `awsl-gateway.groupFilter` — 群名正则过滤
- `awsl-gateway.messageFilter` — 消息正则过滤

## 运行

```bash
openclaw --config openclaw.json
```

## 本地插件

`index.js` 为本地 Channel Plugin，无需发布到 npm。`openclaw.json` 中 `plugins` 指向当前目录。
