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

Chatlog database APIs are not served by Gateway. Use the `awsl-wechat-chatlog`
skill and its API reference for groups, messages, decryption, and media access.

## Error Handling

- `401`: missing or invalid Bearer token when authentication is enabled.
- `422`: invalid request payload, such as an unsupported `message_type` or a file message without `filename`.
- `503`: send queue full.
