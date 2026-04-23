# AWSL WeChat Gateway HTTP API

The CLI accepts an HTTP(S) REST base URL only, such as `http://127.0.0.1:8000` or `https://gateway.example.com`. Use the same HTTP(S) form for `--base-url`, `AWSL_GATEWAY_URL`, and `GATEWAY_URL`. Convert any Gateway WebSocket endpoint to its HTTP(S) origin before invoking the CLI.

Use `Authorization: Bearer <token>` when a token is available. Omit the header when the Gateway is configured for unauthenticated use.

## Endpoints

`GET /api/health`

- Returns Gateway health and group count.

`GET /api/groups`

- Lists currently detected/listened WeChat chat windows.
- Response: `[{ "name": "群名", "active": true }]`
- Use `name` as `group_name` for sending messages.

`POST /api/send`

- Queues a message to a detected chat window.
- Request:

```json
{
  "group_name": "群名",
  "message_type": "text",
  "message": "hello",
  "filename": null
}
```

- `message_type`: `text`, `image`, or `file`.
- `image` and `file` use base64 content in `message`.
- `file` requires `filename`.
- Response: `{ "success": true, "message": "消息已加入发送队列" }`

`GET /api/chatlog/groups?limit=0`

- Lists groups from decrypted WeChat DB output.
- Response fields: `username`, `owner`, `remark`, `nick_name`, `display_name`.
- Use `username` as `group` for message queries.

`POST /api/chatlog/decrypt`

- Decrypts WeChat DB files on the Gateway machine.
- Uses Gateway-side config values: `WECHAT_DB_INPUT_PATH`, `WECHAT_DB_KEY`, and `WECHAT_DB_OUTPUT_PATH`.
- Response: `{ "success": true, "message": "...", "count": 3, "output_path": "..." }`

`GET /api/chatlog/messages`

- Query parameters:
- `group`: chatroom ID or personal WeChat ID, usually `username` from `/api/chatlog/groups`.
- `start`: optional `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS`.
- `end`: optional `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS`.
- `limit`: optional max count, default `100`.

- Response fields: `seq`, `time`, `talker`, `sender`, `sender_name`, `msg_type`, `content`, `is_self`.

## Error Handling

- `400`: invalid input, missing WeChat DB config, missing decrypted DB output, invalid date.
- `401/403`: token/auth failure.
- `404`: chatlog file or group lookup issue.
- `503`: send queue full.
