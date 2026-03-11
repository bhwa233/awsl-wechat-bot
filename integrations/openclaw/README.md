# OpenClaw 接入 AWSL Gateway

OpenClaw (Node.js) 作为 WS 客户端连接 Gateway，利用 ClawHub Skills 生态。

## 安装

```bash
cd integrations/openclaw
npm install
```

## 配置

编辑 `openclaw.json`：

```json
{
  "plugins": ["./"],
  "awsl-gateway": {
    "wsUrl": "ws://127.0.0.1:8000/ws",
    "token": "YOUR_TOKEN",
    "groupFilter": "",
    "messageFilter": ""
  }
}
```

## 运行

```bash
openclaw --config openclaw.json
```

## 本地插件

`index.js` 为本地 Channel Plugin，无需发布到 npm。`openclaw.json` 中 `plugins` 指向当前目录。
