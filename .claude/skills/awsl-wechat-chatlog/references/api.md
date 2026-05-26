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
- `limit` (int, default 100): Max messages

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
- `content` (string, required for `msg_type` 47 / 49): the original message XML payload
- `hd` (bool, default false): image only; high-resolution/original local source (wxgf/HEVC -> JPEG transcode)
- `token` (string, optional): query-string auth fallback for `<img>` / `<audio>` tags

**Usage matrix:**

| msg_type | Required params | Notes |
|---|---|---|
| 3 image | `group`, `server_id`, `hd?` | Needs `IMAGE_AES_KEY`. Default returns thumbnail JPEG; `hd=1` tries original/HD local `.dat` and transcodes wxgf/HEVC to JPEG |
| 34 voice | `server_id` | Needs `pilk` + `av`. Returns MP3 |
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

# voice
curl -OJ "...?msg_type=34&server_id=..." -H "Authorization: ..."

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
