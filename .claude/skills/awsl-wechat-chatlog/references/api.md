# Chatlog Service API Reference

Base URL: `http://localhost:7998` (default)

Authentication: `Authorization: Bearer <HTTP_API_TOKEN>` (same token as Gateway/Cortex)

## Endpoints

### `GET /api/chatlog/health`

Health check (no auth required). Exposes backend type and rich-media capabilities.

**Response:**
```json
{
  "status": "ok",
  "backend": "v2",
  "native_library": "/path/to/awsl_wechat_chatlog.dll",
  "media": {
    "image": true,
    "sticker": true,
    "video_thumb": true,
    "voice": false,
    "file": true
  }
}
```

Capability fields:
- `image` / `video_thumb`: require `IMAGE_AES_KEY` in `.env` (derive via `tools/extract_wx_image_key_*.py`)
- `sticker`: always available
- `voice`: requires `av` + `pilk` runtime dependencies (now required project dependencies)
- `file`: requires `WECHAT_DB_INPUT_PATH` resolvable (file is stored as plaintext, no AES key needed)

---

### `POST /api/chatlog/decrypt`

Decrypt WeChat database files. Paths and key are read from the service's `.env` config (`WECHAT_DB_INPUT_PATH`, `WECHAT_DB_KEY`, `WECHAT_DB_OUTPUT_PATH`).

**Response:**
```json
{
  "success": true,
  "message": "成功解密 3 个文件",
  "count": 3,
  "output_path": "/path/to/decrypted"
}
```

---

### `GET /api/chatlog/groups`

List all chat groups from the decrypted contact database.

**Query parameters:**
- `limit` (int, default 0): Max groups to return. 0 = unlimited.

**Response:** Array of:
```json
{
  "username": "12345@chatroom",
  "owner": "wxid_xxx",
  "remark": "我的群",
  "nick_name": "群名称",
  "display_name": "我的群"
}
```

---

### `GET /api/chatlog/messages`

Query chat messages for a specific group or contact.

**Query parameters:**
- `group` (string, required): Group ID (`xxx@chatroom`) or personal WeChat ID
- `start` (string, optional): Start time (`YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS`)
- `end` (string, optional): End time (same format)
- `limit` (int, default 100): Max messages.
- `offset` (int, default 0): Skip N messages after ordering.
- `desc` (bool, default false): `true` returns newest first; `false` returns oldest first. The skill CLI always sends this parameter explicitly through its required `--desc` or `--asc` flag.

**Response:** Array of:
```json
{
  "seq": 12345,
  "time": "2026-05-04T10:30:00",
  "talker": "12345@chatroom",
  "sender": "wxid_xxx",
  "sender_name": "张三",
  "msg_type": 1,
  "content": "消息内容",
  "is_self": false
}
```

---

### `POST /api/chatlog/messages/search`

Search messages for a specific group or contact (text by default). At least one non-empty `query`
or one `senders` entry is required. When `query` is present, matching is performed in
SQL against the visible message body after legacy group-sender prefix parsing;
Zstd-compressed message bodies are not scanned. Sender-only searches filter by sender
and time in SQL without requiring a content scan.

**JSON request body:**
```json
{
  "group": "12345@chatroom",
  "query": "",
  "senders": ["wxid_alice", "wxid_bob"],
  "start": "2026-05-01",
  "end": "2026-05-04 23:59:59",
  "limit": 100,
  "offset": 0,
  "desc": true
}
```

Fields:
- `group` (string, required): Group ID (`xxx@chatroom`) or personal WeChat ID
- `query` (string, default `""`): Optional literal text to find, at most 256 characters; SQL wildcard characters are treated literally. May be empty only when `senders` is non-empty
- `senders` (array of strings, default `[]`): Optional sender wxids, at most 200; an empty array matches all senders
- `start` / `end` (string, optional): `YYYY-MM-DD` or `YYYY-MM-DD HH:MM:SS`
- `msg_types` (array of integers, default `[1]`): Low 32-bit message types, at most 200 entries; `[]` selects all. Common values: 1 text, 3 image, 34 voice, 43 video, 47 sticker, 49 app message (including files). With `query`, only uncompressed text is matched, intersected with this filter.
- `limit` (int, default 100): Maximum results, 1–10000
- `offset` (int, default 0): Skip results after ordering, 0–100000
- `desc` (bool, default false): `true` returns newest first

**Response:** Same array shape as `GET /api/chatlog/messages`.

---

### `POST /api/chatlog/messages/count`

Accepts `group`, `query`, `senders`, `start`, `end`, and `msg_types` with the same
semantics as search. Only `group` is required; time-only counting is supported.
There is no pagination or ordering. Default `msg_types` is `[1]`; pass `[]` for all types.

```json
{"group":"12345@chatroom","start":"2026-09-01","end":"2026-09-11","senders":["wxid_alice"],"msg_types":[]}
```

Response: `{"count":123,"by_sender":{"wxid_alice":100,"wxid_bob":23}}`.
`count` remains the total after all filters; `by_sender` is aggregated by sender wxid
across databases with the same sender expression used for filtering (including legacy
uncompressed group prefixes). Only matching senders appear, with unknown senders under
`""`; no matches returns `{"count":0,"by_sender":{}}`. No nickname lookup is performed.
The CLI and Python `ChatlogClient.count_messages` return the complete response
(total and map), without an additional grouping parameter.
Counts are summed across message databases using SQL.
Database read failures return an error rather than a partial count.
Both search and count use server-local time, inclusive boundaries, and include the
whole end date when only a date is supplied. A reversed time range returns 400.
Both endpoints are available under explicit `/api/chatlog/v1` and `/api/chatlog/v2`
prefixes and the default prefix; Cortex proxies the default endpoints. V2 requires
an authenticated request and a running WeChat process.

---

### `GET /api/chatlog/group-display-name`

Get the display name for a group.

**Query parameters:**
- `group` (string, required): Group ID

**Response:**
```json
{
  "group": "12345@chatroom",
  "display_name": "我的群"
}
```

---

### `GET /api/chatlog/media`

Fetch decoded rich-media bytes for a chat message. Returns the raw payload with
`X-Media-Md5` / `X-Media-Format` headers + `Cache-Control: immutable` + ETag.
File responses additionally carry `Content-Disposition` for browser downloads.

**Query parameters:**
- `msg_type` (int, required): `3` image, `34` voice, `43` video thumbnail, `47` sticker, `49` file
- `group` (string, required for `msg_type` 3 / 43): group ID or peer wxid
- `server_id` (string, required for `msg_type` 3 / 34 / 43): message `server_id_str` from `/messages`
- `local_id` + `create_time` (int, optional pair for `msg_type` 34): self-sent voice fallback fields from `/messages`; provide both or neither
- `content` (string, required for `msg_type` 47 / 49): the original message XML payload
- `hd` (bool, default false): image only; high-resolution/original local source (wxgf/HEVC -> JPEG transcode)
- `token` (string, optional): query-string auth fallback for `<img>` / `<audio>` tags

**Usage matrix:**

| msg_type | Required params | Notes |
|---|---|---|
| 3 image | `group`, `server_id`, `hd?` | Needs `IMAGE_AES_KEY`. Default returns thumbnail JPEG; `hd=1` tries original/HD local `.dat` and transcodes wxgf/HEVC to JPEG |
| 34 voice | `server_id`; optional `local_id` + `create_time` | Needs `pilk` + `av`. Returns MP3. Looks up `server_id` first, then uniquely falls back to `local_id` + `create_time` for self-sent voice |
| 43 video thumb | `group`, `server_id` | Needs `IMAGE_AES_KEY`. Returns JPEG; the full video binary is NOT served |
| 47 sticker | `content` | Fetched from WeChat CDN, fallback to local `emoticon.db` AES key. Returns GIF/WebP |
| 49 file | `content` | App-msg `<type>6</type>` plaintext file under `msg/file/<YYYY-MM>/<title>`. Returns raw bytes + `Content-Disposition: attachment; filename*=UTF-8'…'`. Max 200 MB |

**Image original/HD behavior (`msg_type=3&hd=1`):**

1. Read the message from `/api/chatlog/messages`.
2. Use `talker` as `group` and `server_id_str` as `server_id`.
3. Chatlog resolves `server_id` to image md5 from `message_resource.db`.
4. It searches local WeChat files under `xwechat_files/<wxid>/msg/attach/<md5(group)>/*/Img/`.
5. Candidate priority is `<md5>.dat`, `_h.dat`, `_W.dat`, other non-thumbnail variants, then `_t.dat`.
6. If the chosen original is WeChat `wxgf`/HEVC, Chatlog converts it to JPEG via `av`.

If `hd=1` returns the same bytes/dimensions as thumbnail, the local WeChat client likely only has `_t.dat`, or the original `.dat` is present but not decodable. SSH to the Chatlog host and inspect the candidate files by md5 before assuming the URL is wrong.

**Unsupported / special cases:**

- Forwarded chat-record cards (`msg_type=49`, appmsg `<type>19</type>`) may contain embedded `recorditem -> dataitem datatype=2` images with `cdndataurl`, `cdndatakey`, and `fullmd5`. The current `/media` endpoint does not expose those embedded record images as ordinary `msg_type=3` images.
- Quote/refer messages (`msg_type=49`, appmsg `<type>57</type>`) often contain only the quoted summary. Do not use the outer `server_id` as a media `server_id` unless the outer message itself is a supported media type.

**Examples:**

```bash
# image (thumbnail)
curl -OJ "http://127.0.0.1:7998/api/chatlog/media?msg_type=3&group=12345@chatroom&server_id=6349274343476749755" \
  -H "Authorization: Bearer $HTTP_API_TOKEN"

# image (HD)
curl -OJ "...&msg_type=3&group=...&server_id=...&hd=1" -H "Authorization: ..."

# image (HD/original via browser token fallback)
open "http://127.0.0.1:7998/api/chatlog/media?msg_type=3&group=12345@chatroom&server_id=6349274343476749755&hd=1&token=$HTTP_API_TOKEN"

# voice (include local_id + create_time so self-sent voice can use its fallback)
curl -OJ "...?msg_type=34&server_id=...&local_id=...&create_time=..." -H "Authorization: ..."

# video thumbnail
curl -OJ "...?msg_type=43&group=...&server_id=..." -H "Authorization: ..."

# sticker (URL-encode the XML content from /messages)
curl -OJ "...?msg_type=47&content=<URL-encoded XML>" -H "Authorization: ..."

# file (download, keeps original Chinese filename via Content-Disposition)
curl -OJ "...?msg_type=49&content=<URL-encoded XML>" -H "Authorization: ..."
```

**Errors:**
- `400`: missing required params, or `msg_type` not in {3,34,43,47,49}
- `404`: message exists but media file is missing on disk (e.g., the user never downloaded the file in WeChat)
- `501`: `IMAGE_AES_KEY` not configured (image / video_thumb only)
