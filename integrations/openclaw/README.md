# OpenClaw 接入 AWSL Gateway

OpenClaw (Node.js) 作为 WS 客户端连接 Gateway，利用 ClawHub Skills 生态。

## 部署模式

### 模式 A：本地直连（OpenClaw 与 Gateway 同机器）

```
OpenClaw ──WS──→ Gateway (localhost:8000)
```

### 模式 B：云端部署（通过 Tailscale 组网）

```
OpenClaw (云端) ──WS──→ Gateway (本地, Tailscale IP:8000)
```

两端装 Tailscale 即可互通，流量端到端加密，无需暴露公网端口。

#### Tailscale 配置

```bash
# 两台机器各执行一次
curl -fsSL https://tailscale.com/install.sh | sh
tailscale up
```

查看本地机器的 Tailscale IP：

```bash
tailscale ip -4   # 如 100.64.x.x
```

OpenClaw 的 `wsUrl` 填 Tailscale IP 即可。

## 安装

```bash
cd integrations/openclaw
npm install
```

## 配置

编辑 `openclaw.json`：

### 本地直连

```json
{
  "plugins": ["./"],
  "awsl-gateway": {
    "wsUrl": "ws://127.0.0.1:8000/ws?token=YOUR_TOKEN",
    "groupFilter": "",
    "messageFilter": ""
  }
}
```

### 云端部署（Tailscale）

```json
{
  "plugins": ["./"],
  "awsl-gateway": {
    "wsUrl": "ws://100.64.x.x:8000/ws?token=YOUR_TOKEN",
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
