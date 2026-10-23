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
- `mention_all`: optional boolean, default `false`, text only, Windows/Linux only. Types `@` and clicks the first “所有人” / “Mention All” candidate without searching, then sends the body. Requires an available owner/admin candidate. Missing/disabled candidates stop the send without falling back to literal text. A body prefix `@所有人` / `@all` does not enable it. The same option is accepted by WS `send_message` and Console `/api/console/send-message` (using `chat_name`). Confirm native delivery through Chatlog `at_users` containing `notify@all`.
- `image` and `file` use base64 content in `message`.
- `file` requires `filename`.
- `voice` uses plain text in `message`, supports Windows/Linux with the required audio devices, and defaults to at most 1,000 input characters and 60 seconds of synthesized audio.
- `voice_file` uses Base64 MP3 in `message`, requires an `.mp3` `filename`, supports Windows/Linux with the required audio devices, and is limited to `BINARY_MAX_FILE_BYTES` decoded bytes (default 32 MiB) and 60 seconds.
- Response: `{ "success": true, "message": "消息已加入发送队列" }`
- Queue admission is not delivery confirmation. Query Chatlog for a matching self-sent record before reporting success or retrying.
- Linux uses X11 plus AT-SPI, and PulseAudio virtual microphones for voice. See the repository README Linux setup for desktop-session and headless-audio prerequisites.
- Linux text beginning with `@name ` attempts native member selection. Missing/ambiguous candidates fall back to the original literal text; confirm native mentions through Chatlog `at_users`.

`POST /api/send/file?group_name=<window_name>&filename=<basename>`

- Prefer this endpoint for file attachments. Send raw file bytes as the request body, with `Content-Type: application/octet-stream`; do not use JSON, Base64, or multipart form data.
- The CLI selects it for `send --message-type file --file-path <path>`. It streams the open file with a Content-Length header. `--filename` overrides the uploaded name; pre-encoded `--message` still uses the original Base64 endpoint.
- Limits: 100 MiB per file, 256 MiB of temporary streamed files owned by the current Gateway process, five concurrent uploads, and a 300-second upload timeout. Queued files and files awaiting deletion count toward the storage limit; previous-process leftovers are outside this quota. The health field `capabilities.max_binary_file_bytes` describes the existing Base64 limit, not this streaming limit.
- Use a nonempty portable basename of at most 200 UTF-8 bytes: no path separators, Windows reserved names, control characters, or trailing spaces/dots. Empty files are rejected.
- Reception writes directly to one final temporary file. The API cleans up after sending: Windows waits 60 seconds, Linux 180 seconds, and macOS deletes immediately. Interrupted uploads, rejected queue entries, and failed sends are cleaned up immediately. Failed deletions retry ten times at one-second intervals and retain the storage reservation if all attempts fail. Process termination can leave temporary files behind.
- HTTP 200 means queued, not delivered. Confirm a matching self-sent Chatlog record before reporting success or retrying; attachment bytes can be downloaded through Chatlog to verify their hash. Do not automatically retry through Base64.
- The original `/api/send` and WS Base64 interfaces remain available. This raw file endpoint is served by Gateway, without a Cortex proxy or Console equivalent.

Chatlog database APIs are not served by Gateway. Use the `awsl-wechat-chatlog`
skill and its API reference for groups, messages, decryption, and media access.

## Error Handling

- `401`: missing or invalid Bearer token when authentication is enabled.
- `400`: streaming upload has an invalid filename, an empty body, or an interrupted body.
- `408`: streaming upload exceeded 300 seconds.
- `413`: streaming file exceeded 100 MiB.
- `422`: invalid request payload, such as an unsupported `message_type` or a file message without `filename`.
- `429`: five streaming uploads are already active.
- `501`: the current Gateway platform does not support the requested message type.
- `503`: send queue full, or the streamed temporary storage budget is exhausted.
- `507`: unable to write a streamed temporary file.
