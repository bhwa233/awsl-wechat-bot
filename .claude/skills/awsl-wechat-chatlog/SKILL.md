---
name: awsl-wechat-chatlog
description: "Use when Codex needs to operate an AWSL WeChat Chatlog service by HTTP API: connect with a Chatlog base URL and optional Bearer token, decrypt WeChat chat records, list decrypted chatlog groups and messages, or query group display names."
---

# AWSL WeChat Chatlog

## Quick Start

Run commands from the repository root. Use the bundled CLI through `uv run`; dependencies are declared inline in the script:

```bash
# First-time setup: cache the Chatlog base URL (and optional token)
uv run ".claude/skills/awsl-wechat-chatlog/scripts/awsl_chatlog.py" \
  config set --base-url http://127.0.0.1:7998 --token "$HTTP_API_TOKEN"

# Subsequent calls reuse the cached connection automatically
uv run ".claude/skills/awsl-wechat-chatlog/scripts/awsl_chatlog.py" groups
```

Connection info (`base_url`, `token`) is cached at `~/.config/awsl-wechat-chatlog/config.json` (chmod 600). The CLI accepts an HTTP(S) REST base URL only. The token is optional. When omitted, the CLI sends no `Authorization` header.

Resolution order: explicit `--base-url` / `--token` flags > cached config. Passing `--base-url` or `--token` will refresh the cache; add `--no-save` to skip writing.

## Capabilities

- Manage cached connection info with `config show|set|clear`.
- Decrypt WeChat chat records with `decrypt`, which calls `POST /api/chatlog/decrypt` and uses Chatlog-side `.env` values.
- List decrypted chatlog groups with `groups`, which calls `GET /api/chatlog/groups`.
- Query chat records with `messages --group <group_id>`, which calls `GET /api/chatlog/messages`. Use the `username` from `groups` as `--group`.
- Get group display name with `group-name --group <group_id>`, which calls `GET /api/chatlog/group-display-name`.
- Health check with `health`, which calls `GET /api/chatlog/health`. Response includes `media` capability flags: `image`, `sticker`, `video_thumb`, `voice`, `file`.

The Chatlog service also exposes `GET /api/chatlog/media` for fetching rich-media bytes
(images, voice, video thumbnails, stickers, files). Not wrapped by this CLI — invoke via
`curl` / browser, see `references/api.md` for the full parameter matrix.

## Examples

```bash
# Cache connection details once
uv run ".claude/skills/awsl-wechat-chatlog/scripts/awsl_chatlog.py" \
  config set --base-url http://127.0.0.1:7998 --token "$HTTP_API_TOKEN"

# Inspect or remove the cached connection
uv run ".claude/skills/awsl-wechat-chatlog/scripts/awsl_chatlog.py" config show
uv run ".claude/skills/awsl-wechat-chatlog/scripts/awsl_chatlog.py" config clear

# Decrypt WeChat DB records
uv run ".claude/skills/awsl-wechat-chatlog/scripts/awsl_chatlog.py" decrypt

# Find chatlog group IDs
uv run ".claude/skills/awsl-wechat-chatlog/scripts/awsl_chatlog.py" groups --limit 20

# Query records for a group ID
uv run ".claude/skills/awsl-wechat-chatlog/scripts/awsl_chatlog.py" messages \
  --group 12345@chatroom --start "2026-04-23" --limit 50

# Get group display name
uv run ".claude/skills/awsl-wechat-chatlog/scripts/awsl_chatlog.py" group-name \
  --group 12345@chatroom

# Download a file attachment (msg_type=49 type=6) via curl using the message's content XML
# Get server_id_str + content from `messages`, then:
curl -OJ -H "Authorization: Bearer $HTTP_API_TOKEN" \
  "$CHATLOG_BASE/api/chatlog/media?msg_type=49&content=$(python3 -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.stdin.read()))' < content.xml)"
# -OJ keeps the original filename returned in Content-Disposition (UTF-8 + ASCII fallback)
```

## Chatlog Service Notes

The Chatlog service is a standalone data layer that reads decrypted WeChat databases. It does NOT perform LLM summarization or message sending — those are handled by the Cortex service. For endpoint details, read `references/api.md`.
