# AWSL WeChat Gateway HTTP API

The CLI accepts an HTTP(S) REST base URL only, such as `http://127.0.0.1:8000` or `https://gateway.example.com`. Use the same HTTP(S) form for `--base-url`, `AWSL_GATEWAY_URL`, and `GATEWAY_URL`. Convert any Gateway WebSocket endpoint to its HTTP(S) origin before invoking the CLI.

Use `Authorization: Bearer <token>` when a token is available. Omit the header when the Gateway is configured for unauthenticated use.

## Endpoints

`GET /api/health`

- Returns Gateway health, group count, and `capabilities.voice` platform support.

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

- `message_type`: `text`, `image`, `file`, `voice`, or `voice_file`.
- `image` and `file` use base64 content in `message`.
- `file` requires `filename`.
- `voice` uses plain text in `message`, supports Windows/Linux with the required audio devices, and defaults to at most 1,000 input characters and 60 seconds of synthesized audio.
- `voice_file` uses Base64 MP3 in `message`, requires an `.mp3` `filename`, supports Windows/Linux with the required audio devices, and is limited to `BINARY_MAX_FILE_BYTES` decoded bytes (default 32 MiB) and 60 seconds.
- Response: `{ "success": true, "message": "消息已加入发送队列" }`
- Queue admission is not delivery confirmation. Query Chatlog for a matching self-sent record before reporting success or retrying.
- Linux uses X11 plus AT-SPI, and PulseAudio virtual microphones for voice. See the repository README Linux setup for desktop-session and headless-audio prerequisites.
- Linux text beginning with `@name ` attempts native member selection. Missing/ambiguous candidates fall back to the original literal text; confirm native mentions through Chatlog `at_users`.

Chatlog database APIs are not served by Gateway. Use the `awsl-wechat-chatlog`
skill and its API reference for groups, messages, decryption, and media access.

## Error Handling

- `401`: missing or invalid Bearer token when authentication is enabled.
- `422`: invalid request payload, such as an unsupported `message_type` or a file message without `filename`.
- `501`: the current Gateway platform does not support the requested message type.
- `503`: send queue full.
