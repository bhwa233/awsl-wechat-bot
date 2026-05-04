# Chatlog Service API Reference

Base URL: `http://localhost:7998` (default)

Authentication: `Authorization: Bearer <HTTP_API_TOKEN>` (same token as Gateway/Cortex)

## Endpoints

### `GET /api/chatlog/health`

Health check (no auth required).

**Response:** `{"status": "ok"}`

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
