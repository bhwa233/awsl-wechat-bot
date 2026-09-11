---
name: awsl-wechat-chatlog
description: "Use when Codex needs to operate an AWSL WeChat Chatlog service by HTTP API: connect with a Chatlog base URL and optional Bearer token, decrypt WeChat chat records, list, search, or count chatlog messages, list groups, or query group display names."
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
- Query chat records with `messages --group <group_id>`, which calls `GET /api/chatlog/messages`. Every `messages` command must explicitly pass exactly one ordering flag: `--desc` for newest first or `--asc` for oldest first. With `--desc`, `--limit 100` returns the latest 100 messages. Use the `username` from `groups` as `--group`.
- Search chat records (text by default) with `search-messages --group <group_id>`, which calls `POST /api/chatlog/messages/search`. Pass `--query <text>`, repeat `--sender <wxid>`, or combine both; at least one query or sender is required. Exactly one of `--desc` or `--asc` is required.
- Count messages with `count-messages --group <group_id>`, which calls `POST /api/chatlog/messages/count` and returns `{"count": N, "by_sender": {"wxid_alice": N}}`. Time (`--start`, `--end`), repeatable `--sender`, and `--query` are optional; no ordering or pagination flags. `by_sender` is a wxid-to-count map merged across databases. Only senders with matching messages appear; an empty wxid represents an unknown sender.
- Both search and count default to text (type 1). Repeat `--msg-type` to select types, or use `--all-types` for every type. A keyword still matches only uncompressed text, intersected with selected types. Dates use the service's local timezone; an end date includes the whole day.
- Get group display name with `group-name --group <group_id>`, which calls `GET /api/chatlog/group-display-name`.
- Health check with `health`, which calls `GET /api/chatlog/health`. Response includes `media` capability flags: `image`, `sticker`, `video_thumb`, `voice`, `file`.

The Chatlog service also exposes `GET /api/chatlog/media` for fetching rich-media bytes
(images, image HD/original, voice, video thumbnails, stickers, files). Not wrapped by
this CLI — invoke via `curl` / browser, see `references/api.md` for the full parameter
matrix.

## Rich Media Quick Reference

Get the source fields from `messages`: use `talker` as `group`, use `server_id_str`
as `server_id`, and use raw `content` XML for stickers/files. For voice, also pass
`local_id` and `create_time`; Chatlog uses them only when self-sent voice cannot be
found by `server_id`.

| Message | URL params |
|---|---|
| Image thumbnail (`msg_type=3`) | `msg_type=3&group=<talker>&server_id=<server_id_str>` |
| Image HD/original (`msg_type=3`) | `msg_type=3&group=<talker>&server_id=<server_id_str>&hd=1` |
| Video thumbnail (`msg_type=43`) | `msg_type=43&group=<talker>&server_id=<server_id_str>` |
| Voice (`msg_type=34`) | `msg_type=34&server_id=<server_id_str>&local_id=<local_id>&create_time=<create_time>` |
| Sticker (`msg_type=47`) | `msg_type=47&content=<urlencoded content XML>` |
| File attachment (`msg_type=49`, appmsg type 6) | `msg_type=49&content=<urlencoded content XML>` |

Image HD/original is local-disk based: Chatlog resolves `server_id` -> image md5 via
`message_resource.db`, then tries local `.dat` files under
`xwechat_files/<wxid>/msg/attach/<md5(talker)>/*/Img/`. Candidate priority is original
`<md5>.dat`, `_h.dat`, `_W.dat`, other non-thumbnail variants, then `_t.dat`. If the
original is WeChat `wxgf`/HEVC, the service transcodes it to JPEG via `av`.

If `hd=1` returns the same bytes/dimensions as the thumbnail, inspect the remote disk:
usually the client has only cached `_t.dat`, or the original candidate cannot be
decoded. Current required dependencies include `av`, `pilk`, and `numpy`, so normal
`uv sync` should keep HEVC image and voice support installed.

Forwarded chat-record cards (`msg_type=49`, appmsg type 19) can contain embedded
`recorditem -> dataitem datatype=2` image metadata (`cdndataurl`, `cdndatakey`,
`fullmd5`). The current `/media` endpoint does not expose those embedded record images
as ordinary `msg_type=3` media; do not treat the outer card's `server_id` as an image
`server_id`.

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

# Query the latest 50 records for a group ID (newest first)
uv run ".claude/skills/awsl-wechat-chatlog/scripts/awsl_chatlog.py" messages \
  --group 12345@chatroom --start "2026-04-23" --limit 50 --desc

# Query the same range oldest first
uv run ".claude/skills/awsl-wechat-chatlog/scripts/awsl_chatlog.py" messages \
  --group 12345@chatroom --start "2026-04-23" --limit 50 --asc

# Search visible plain-text message bodies from selected senders
uv run ".claude/skills/awsl-wechat-chatlog/scripts/awsl_chatlog.py" search-messages \
  --group 12345@chatroom --query "发布" \
  --sender wxid_alice --sender wxid_bob --limit 50 --desc

# Query all text messages from one sender in a time range (no keyword required)
uv run ".claude/skills/awsl-wechat-chatlog/scripts/awsl_chatlog.py" search-messages \
  --group 12345@chatroom --sender wxid_alice \
  --start "2026-05-01" --end "2026-05-04" --limit 1000 --asc

# Count messages per sender, including all message types
uv run ".claude/skills/awsl-wechat-chatlog/scripts/awsl_chatlog.py" count-messages \
  --group 12345@chatroom --start "2026-09-01" --end "2026-09-11" \
  --all-types

# Find images and stickers from a sender
uv run ".claude/skills/awsl-wechat-chatlog/scripts/awsl_chatlog.py" search-messages \
  --group 12345@chatroom --sender wxid_alice --msg-type 3 --msg-type 47 --desc

# Count all types in a time range (omit --sender to count everyone)
uv run ".claude/skills/awsl-wechat-chatlog/scripts/awsl_chatlog.py" count-messages \
  --group 12345@chatroom --sender wxid_alice --all-types \
  --start "2026-09-01" --end "2026-09-11"

# Get group display name
uv run ".claude/skills/awsl-wechat-chatlog/scripts/awsl_chatlog.py" group-name \
  --group 12345@chatroom

# Download an image HD/original. Get talker + server_id_str from `messages`.
curl -OJ -H "Authorization: Bearer $HTTP_API_TOKEN" \
  "$CHATLOG_BASE/api/chatlog/media?msg_type=3&group=12345@chatroom&server_id=6349274343476749755&hd=1"

# Download a file attachment (msg_type=49 type=6) via curl using the message's content XML
# Get server_id_str + content from `messages`, then:
curl -OJ -H "Authorization: Bearer $HTTP_API_TOKEN" \
  "$CHATLOG_BASE/api/chatlog/media?msg_type=49&content=$(python3 -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.stdin.read()))' < content.xml)"
# -OJ keeps the original filename returned in Content-Disposition (UTF-8 + ASCII fallback)
```

## Chatlog Service Notes

The Chatlog service is a standalone data layer that reads decrypted WeChat databases. It does NOT perform LLM summarization or message sending — those are handled by the Cortex service. For endpoint details, read `references/api.md`.

The bundled CLI is a Python 3.11+ script with no third-party Python dependencies. It uses only the standard library to call the Chatlog HTTP API; SQL execution remains inside the Chatlog service.
