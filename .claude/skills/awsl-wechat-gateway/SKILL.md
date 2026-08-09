---
name: awsl-wechat-gateway
description: "Use when Codex needs to operate an AWSL WeChat Bot Gateway by HTTP API: connect with a Gateway base URL and optional Bearer token, list open/listened WeChat chat windows, or send text/image/file/voice messages through the Gateway."
---

# AWSL WeChat Gateway

## Quick Start

Run commands from the repository root. Use the bundled CLI through `uv run`; dependencies are declared inline in the script:

```bash
# First-time setup: cache the Gateway base URL (and optional token)
uv run ".claude/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" \
  config set --base-url http://127.0.0.1:8000 --token "$HTTP_API_TOKEN"

# Subsequent calls reuse the cached connection automatically
uv run ".claude/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" windows
```

Connection info (`base_url`, `token`) is cached at `~/.config/awsl-wechat-gateway/config.json` (chmod 600). The CLI accepts an HTTP(S) REST base URL only, such as `http://127.0.0.1:8000` or `https://gateway.example.com`. Convert any Gateway WebSocket endpoint to its HTTP(S) origin before invoking the CLI. The token is optional. When omitted, the CLI sends no `Authorization` header.

Resolution order: explicit `--base-url` / `--token` flags > cached config. Passing `--base-url` or `--token` will refresh the cache; add `--no-save` to skip writing.

## Capabilities

- Manage cached connection info with `config show|set|clear`.
- List open/listened chat windows with `windows`, which calls `GET /api/groups`.
- Send messages with `send --group <window_name> --message <text>`. Use the visible/open window name from `windows` as `--group`.
- Send Windows voice messages with `send --group <window_name> --message-type voice --message <text>`; Gateway synthesizes the text with EdgeTTS and limits output to 60 seconds.

**Note:** Chatlog features (decrypt, groups, messages) have been moved to the `awsl-wechat-chatlog` skill. Use that skill for WeChat database operations.

## Examples

```bash
# Cache connection details once, then drop --base-url/--token from later calls
uv run ".claude/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" \
  config set --base-url https://wxbot-gateway.home.dreamhunter.uk --token "$HTTP_API_TOKEN"

# Inspect or remove the cached connection
uv run ".claude/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" config show
uv run ".claude/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" config clear

# List currently detected WeChat windows (uses cached connection)
uv run ".claude/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" windows

# Override the cached base URL for a single call without touching the cache
uv run ".claude/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" \
  --base-url http://127.0.0.1:8000 --no-save windows

# Send text to an open chat window
uv run ".claude/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" send \
  --group "测试群" --message "收到"

# Send a local image or file; the helper base64-encodes file contents
uv run ".claude/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" send \
  --group "测试群" --message-type image --file-path ./image.png

# Send synthesized voice on a Windows Gateway
uv run ".claude/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" send \
  --group "测试群" --message-type voice --message "你好，这是一条语音消息"
```

## Gateway Notes

For endpoint details and response shapes, read `references/api.md`. Window sending requires the destination chat window to be detected by Gateway.
