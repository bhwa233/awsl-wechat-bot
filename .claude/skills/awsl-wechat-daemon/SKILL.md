---
name: awsl-wechat-daemon
description: "Use when Codex needs to operate an AWSL WeChat Bot Daemon by HTTP API: connect with a Daemon base URL and optional Bearer token, check bot status, start/stop/restart the bot process, trigger git pull + uv sync updates, or tail bot logs."
---

# AWSL WeChat Daemon

## Quick Start

Run commands from the repository root. Use the bundled CLI through `uv run`; dependencies are declared inline in the script:

```bash
# First-time setup: cache the Daemon base URL (and optional token)
uv run ".claude/skills/awsl-wechat-daemon/scripts/awsl_daemon.py" \
  config set --base-url http://127.0.0.1:8001 --token "$HTTP_API_TOKEN"

# Subsequent calls reuse the cached connection automatically
uv run ".claude/skills/awsl-wechat-daemon/scripts/awsl_daemon.py" status
```

Connection info (`base_url`, `token`) is cached at `~/.config/awsl-wechat-daemon/config.json` (chmod 600). The CLI accepts an HTTP(S) REST base URL only. The token is optional. When omitted, the CLI sends no `Authorization` header.

Resolution order: explicit `--base-url` / `--token` flags > cached config. Passing `--base-url` or `--token` will refresh the cache; add `--no-save` to skip writing.

## Capabilities

- Manage cached connection info with `config show|set|clear`.
- Check Daemon health with `health`, which calls `GET /health`.
- Get bot process status with `status`, which calls `GET /status`.
- Start the bot with `start`, which calls `POST /start`.
- Stop the bot with `stop`, which calls `POST /stop`.
- Restart the bot with `restart`, which calls `POST /restart`.
- Update and restart with `update`, which calls `POST /update` (stops bot, runs `git pull` + `uv sync`, restarts bot).
- Tail bot logs with `logs --lines N`, which calls `GET /logs?lines=N`.

## Examples

```bash
# Cache connection details once
uv run ".claude/skills/awsl-wechat-daemon/scripts/awsl_daemon.py" \
  config set --base-url http://127.0.0.1:8001 --token "$HTTP_API_TOKEN"

# Inspect or remove the cached connection
uv run ".claude/skills/awsl-wechat-daemon/scripts/awsl_daemon.py" config show
uv run ".claude/skills/awsl-wechat-daemon/scripts/awsl_daemon.py" config clear

# Check daemon health
uv run ".claude/skills/awsl-wechat-daemon/scripts/awsl_daemon.py" health

# Get bot process status (PID, uptime, restart count)
uv run ".claude/skills/awsl-wechat-daemon/scripts/awsl_daemon.py" status

# Start / stop / restart the bot
uv run ".claude/skills/awsl-wechat-daemon/scripts/awsl_daemon.py" start
uv run ".claude/skills/awsl-wechat-daemon/scripts/awsl_daemon.py" stop
uv run ".claude/skills/awsl-wechat-daemon/scripts/awsl_daemon.py" restart

# Pull latest code, sync deps, restart bot
uv run ".claude/skills/awsl-wechat-daemon/scripts/awsl_daemon.py" update

# Tail last 200 lines of bot logs
uv run ".claude/skills/awsl-wechat-daemon/scripts/awsl_daemon.py" logs --lines 200
```

## Daemon Notes

The Daemon controller manages the bot (Gateway + Chatlog + Cortex) as a subprocess. It provides process lifecycle management (start/stop/restart), code updates (git pull + uv sync), and log tailing. For endpoint details, read `references/api.md`.
