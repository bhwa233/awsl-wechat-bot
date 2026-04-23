---
name: awsl-wechat-gateway
description: "Use when Codex needs to operate an AWSL WeChat Bot Gateway by HTTP API: connect with a Gateway base URL and optional Bearer token, decrypt WeChat chat records on the Gateway machine, list open/listened WeChat chat windows, query decrypted WeChat chatlog groups and messages, or send text/image/file messages through the Gateway."
---

# AWSL WeChat Gateway

## Quick Start

Run commands from the repository root. Use the bundled CLI through `uv run`; dependencies are declared inline in the script:

```bash
uv run ".claude/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" \
  --base-url http://127.0.0.1:8000 \
  windows
```

The CLI accepts an HTTP(S) REST base URL only, such as `http://127.0.0.1:8000` or `https://gateway.example.com`. Set `AWSL_GATEWAY_URL` and `GATEWAY_URL` to the same HTTP(S) form. Convert any Gateway WebSocket endpoint to its HTTP(S) origin before invoking the CLI. The token is optional. When omitted, the CLI sends no `Authorization` header. It also reads `AWSL_GATEWAY_TOKEN` or `HTTP_API_TOKEN` from the environment.

## Capabilities

- List open/listened chat windows with `windows`, which calls `GET /api/groups`.
- Decrypt WeChat chat records with `decrypt`, which calls `POST /api/chatlog/decrypt` and uses Gateway-side `.env` values.
- List decrypted chatlog groups with `chatlog-groups`, which calls `GET /api/chatlog/groups`.
- Query chat records with `messages --group <group_id>`, which calls `GET /api/chatlog/messages`. Use the `username` from `chatlog-groups` as `--group`.
- Send messages with `send --group <window_name> --message <text>`. Use the visible/open window name from `windows` as `--group`.

## Examples

```bash
# List currently detected WeChat windows
uv run ".claude/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" -b http://127.0.0.1:8000 windows

# Use an HTTPS Gateway API origin
uv run ".claude/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" -b https://wxbot-gateway.home.dreamhunter.uk windows

# Find chatlog group IDs
uv run ".claude/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" -b http://127.0.0.1:8000 -t "$HTTP_API_TOKEN" chatlog-groups --limit 20

# Decrypt WeChat DB records on the Gateway machine
uv run ".claude/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" -b http://127.0.0.1:8000 -t "$HTTP_API_TOKEN" decrypt

# Query records for a group ID
uv run ".claude/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" -b http://127.0.0.1:8000 messages \
  --group 12345@chatroom --start "2026-04-23" --limit 50

# Send text to an open chat window
uv run ".claude/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" -b http://127.0.0.1:8000 send \
  --group "测试群" --message "收到"

# Send a local image or file; the helper base64-encodes file contents
uv run ".claude/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" -b http://127.0.0.1:8000 send \
  --group "测试群" --message-type image --file-path ./image.png
```

## Gateway Notes

For endpoint details and response shapes, read `references/api.md`. Decrypt and chatlog queries require Gateway-side WeChat database configuration. Window sending requires the destination chat window to be detected by Gateway.
