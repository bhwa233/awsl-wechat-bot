---
name: awsl-wechat-gateway
description: "Use when Codex needs to operate an AWSL WeChat Bot Gateway by HTTP API: connect with a Gateway base URL and optional Bearer token, list open/listened WeChat chat windows, or send text/image/file/TTS voice/MP3 voice messages through the Gateway."
---

# AWSL WeChat Gateway

## Quick Start

Run commands from the repository root. Use the bundled CLI through `uv run`; dependencies are declared inline in the script:

```bash
# First-time setup: cache the Gateway base URL (and optional token)
uv run ".agents/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" \
  config set --base-url http://127.0.0.1:8000 --token "$HTTP_API_TOKEN"

# Subsequent calls reuse the cached connection automatically
uv run ".agents/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" windows
```

Connection info (`base_url`, `token`) is cached at `~/.config/awsl-wechat-gateway/config.json` (chmod 600). The CLI accepts an HTTP(S) REST base URL only, such as `http://127.0.0.1:8000` or `https://gateway.example.com`. Convert any Gateway WebSocket endpoint to its HTTP(S) origin before invoking the CLI. The token is optional. When omitted, the CLI sends no `Authorization` header.

Resolution order: explicit `--base-url` / `--token` flags > cached config. Passing `--base-url` or `--token` will refresh the cache; add `--no-save` to skip writing.

## Capabilities

- Manage cached connection info with `config show|set|clear`.
- List open/listened chat windows with `windows`, which calls `GET /api/groups`.
- Send messages with `send --group <window_name> --message <text>`. Use the visible/open window name from `windows` as `--group`.
- Prefer binary streaming for file attachments: `send --group <window_name> --message-type file --file-path <path>` uses `POST /api/send/file` without loading or Base64-encoding the whole file. Image/MP3 inputs and pre-encoded Base64 `--message` keep using `/api/send`. Do not automatically fall back to Base64 or resend after an upload error; confirm delivery through Chatlog first. See `references/api.md` for limits and cleanup.
- Send a native all-members mention on Windows/Linux with `send --group <window_name> --message <body> --mention-all`. This is a text-only option, requires an available owner/admin all-members candidate, and fails without sending when that candidate is missing. A literal `@所有人` or `@all` prefix does not enable it. Confirm delivery and `at_users` containing `notify@all` through Chatlog.
- Send Windows/Linux TTS voice with `--message-type voice --message <text>` or an MP3 with `--message-type voice_file --file-path <file.mp3>`. Both are limited to 60 seconds. Linux requires PulseAudio; check `GET /api/health` → `capabilities.voice` before sending.

**Note:** Chatlog features (decrypt, groups, messages) have been moved to the `awsl-wechat-chatlog` skill. Use that skill for WeChat database operations.

## Examples

```bash
# Cache connection details once, then drop --base-url/--token from later calls
uv run ".agents/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" \
  config set --base-url https://wxbot-gateway.home.dreamhunter.uk --token "$HTTP_API_TOKEN"

# Inspect or remove the cached connection
uv run ".agents/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" config show
uv run ".agents/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" config clear

# List currently detected WeChat windows (uses cached connection)
uv run ".agents/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" windows

# Override the cached base URL for a single call without touching the cache
uv run ".agents/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" \
  --base-url http://127.0.0.1:8000 --no-save windows

# Send text to an open chat window
uv run ".agents/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" send \
  --group "测试群" --message "收到"

# Prefer binary streaming for a local file (up to 100 MiB)
uv run ".agents/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" --timeout 300 send \
  --group "测试群" --message-type file --file-path ./document.pdf

# Send a local image; image inputs retain Base64 encoding
uv run ".agents/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" send \
  --group "测试群" --message-type image --file-path ./image.png

# Send synthesized voice on a Windows/Linux Gateway
uv run ".agents/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" send \
  --group "测试群" --message-type voice --message "你好，这是一条语音消息"

# Send an existing MP3 as voice on a Windows/Linux Gateway
uv run ".agents/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" send \
  --group "测试群" --message-type voice_file --file-path ./voice.mp3
```

## Gateway Notes

For endpoint details and response shapes, read `references/api.md`. Window sending requires the destination chat window to be detected by Gateway.
