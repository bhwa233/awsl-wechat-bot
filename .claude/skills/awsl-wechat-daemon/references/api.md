# AWSL WeChat Daemon HTTP API

Use `Authorization: Bearer <token>` when a token is available. Omit the header when the Daemon is configured for unauthenticated use.

## Endpoints

`GET /health`

- Returns Daemon health and auth status.
- Response: `{ "ok": true, "auth_enabled": true }`

`GET /status`

- Returns bot process status (PID, uptime, restart count, etc).
- Requires auth.

`POST /start`

- Starts the bot process. Enables auto-restart.
- Requires auth.

`POST /stop`

- Stops the bot process. Disables auto-restart.
- Requires auth.

`POST /restart`

- Restarts the bot process.
- Requires auth.

`POST /update`

- Stops bot, runs `git pull` + `uv sync`, then restarts bot.
- Returns git/uv output and whether daemon itself was updated.
- If daemon code changed, returns a message to manually restart daemon.
- Requires auth. Timeout: long (git pull + uv sync).

`GET /logs?lines=100`

- Returns the last N lines of bot process stdout/stderr.
- Query param `lines`: 1-10000, default 100.
- Requires auth.
- Response: plain text.

## Error Handling

- `401/403`: token/auth failure.
