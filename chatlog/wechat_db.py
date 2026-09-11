#!/usr/bin/env python3
"""
微信聊天记录工具

功能:
1. 解密加密的微信数据库 (V4 版本)
2. 列出群聊名称
3. 按时间范围查询聊天记录

使用方法:
    python wechat_db.py decrypt --input <目录> --key <密钥> --output <输出目录>
    python wechat_db.py list-groups --db-path <解密后目录>
    python wechat_db.py query --db-path <目录> --group <群ID> [--start <时间>] [--end <时间>]
"""

import argparse
import base64
import contextlib
import hashlib
import hmac
import json
import os
import re
import shutil
import sqlite3
import struct
from dataclasses import dataclass, field
from datetime import datetime
from hashlib import pbkdf2_hmac, sha512

from chatlog.models import ChatlogMessageCountResponse
from chatlog.platform import get_chatlog_platform
from chatlog.search import build_message_count, escape_like_query, message_search_expressions, resolve_message_sender

try:
    from Crypto.Cipher import AES
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

try:
    import zstandard as zstd
    HAS_ZSTD = True
except ImportError:
    HAS_ZSTD = False

# ============================================================
# 常量
# ============================================================

KEY_SIZE = 32
SALT_SIZE = 16
IV_SIZE = 16
PAGE_SIZE = 4096
ITER_COUNT = 256000
HMAC_SHA512_SIZE = 64
AES_BLOCK_SIZE = 16
SQLITE_HEADER = b"SQLite format 3\x00"

_reserve = IV_SIZE + HMAC_SHA512_SIZE
RESERVE_SIZE = ((_reserve // AES_BLOCK_SIZE) + 1) * AES_BLOCK_SIZE if _reserve % AES_BLOCK_SIZE != 0 else _reserve

MESSAGE_TYPE_TEXT = 1
MESSAGE_TYPE_IMAGE = 3
MESSAGE_TYPE_VOICE = 34
MESSAGE_TYPE_VIDEO = 43
MESSAGE_TYPE_ANIMATION = 47
MESSAGE_TYPE_LOCATION = 48
MESSAGE_TYPE_SHARE = 49
MESSAGE_TYPE_SYSTEM = 10000


# ============================================================
# 数据模型
# ============================================================

@dataclass
class ChatRoom:
    """群聊信息"""
    username: str
    owner: str
    remark: str = ""
    nick_name: str = ""
    avatar: str = ""        # small_head_url (/132 小图), 腾讯 CDN 直链
    big_avatar: str = ""    # big_head_url (/0 原图)
    user_display_names: dict = None  # username -> display_name 映射

    def __post_init__(self):
        if self.user_display_names is None:
            self.user_display_names = {}

    def display_name(self) -> str:
        return self.remark or self.nick_name or self.username

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "owner": self.owner,
            "remark": self.remark,
            "nick_name": self.nick_name,
            "display_name": self.display_name(),
            "avatar": self.avatar,
            "big_avatar": self.big_avatar,
        }


@dataclass
class Contact:
    """联系人/群成员信息"""
    username: str
    nick_name: str = ""
    remark: str = ""
    alias: str = ""
    avatar: str = ""        # small_head_url (/132 小图)
    big_avatar: str = ""    # big_head_url (/0 原图)

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "nick_name": self.nick_name,
            "remark": self.remark,
            "alias": self.alias,
            "avatar": self.avatar,
            "big_avatar": self.big_avatar,
        }


@dataclass
class RoomMember:
    """群成员信息: 群内昵称(display_name) + 联系人资料(nick_name/头像)"""
    username: str
    display_name: str = ""   # 群内昵称 (来自 chat_room.ext_buffer)
    nick_name: str = ""      # 微信昵称 (来自 contact 表)
    remark: str = ""
    alias: str = ""
    avatar: str = ""         # small_head_url (/132 小图)
    big_avatar: str = ""     # big_head_url (/0 原图)
    is_owner: bool = False   # 是否群主

    def to_dict(self) -> dict:
        return {
            "username": self.username,
            "display_name": self.display_name,
            "nick_name": self.nick_name,
            "remark": self.remark,
            "alias": self.alias,
            "avatar": self.avatar,
            "big_avatar": self.big_avatar,
            "is_owner": self.is_owner,
        }


def _build_room_member(
    username: str,
    display_name: str,
    contact: "Contact | None",
    is_owner: bool,
) -> "RoomMember":
    """组合 ext_buffer 群昵称与 contact 表资料为 RoomMember (v1/v2 共用)"""
    return RoomMember(
        username=username,
        display_name=display_name,
        nick_name=contact.nick_name if contact else "",
        remark=contact.remark if contact else "",
        alias=contact.alias if contact else "",
        avatar=contact.avatar if contact else "",
        big_avatar=contact.big_avatar if contact else "",
        is_owner=is_owner,
    )


@dataclass
class Message:
    """消息"""
    seq: int
    time: datetime
    talker: str
    sender: str
    sender_name: str
    msg_type: int
    content: str
    is_self: bool = False
    server_id: int = 0  # 全局唯一消息 ID, 用于 join MessageResourceInfo 拿明文 md5
    local_id: int = 0  # 本地消息 ID, 自发语音在 VoiceInfo 中通过此字段关联
    at_users: list[str] = field(default_factory=list)  # 群聊被 @ 人的 wxid 列表

    def format(self, time_format: str = "%Y-%m-%d %H:%M:%S") -> str:
        sender_display = "我" if self.is_self else (self.sender_name or self.sender)
        return f"[{self.time.strftime(time_format)}] {sender_display}: {self.content}"

    def to_dict(self) -> dict:
        return {
            "seq": self.seq,
            "time": self.time.isoformat(),
            "talker": self.talker,
            "sender": self.sender,
            "sender_name": self.sender_name,
            "msg_type": self.msg_type,
            "content": self.content,
            "is_self": self.is_self,
            "local_id": self.local_id,
            "create_time": int(self.time.timestamp()),
            "at_users": self.at_users,
        }


def _decode_source_xml(raw) -> str:
    """消息 source 列 → XML 文本.

    v1(sqlite 直读) source 是 zstd 压缩 bytes; v2(原生库) 是 base64(zstd) 字符串.
    短消息可能未压缩, 直接是明文 XML. 本函数统一兼容这几种形态.
    """
    if raw is None:
        return ""
    data = raw
    if isinstance(data, str):
        # v2 原生库把 BLOB 编码为 base64; 明文 XML(含 '<') base64 解码会失败, 原样返回
        try:
            data = base64.b64decode(data, validate=True)
        except Exception:
            return data
    if isinstance(data, (bytes, bytearray)):
        b = bytes(data)
        if len(b) >= 4 and b[:4] == b"\x28\xb5\x2f\xfd" and HAS_ZSTD:
            try:
                return zstd.ZstdDecompressor().decompress(b).decode("utf-8", "ignore")
            except Exception:
                pass
        return b.decode("utf-8", "ignore")
    return str(data)


def _parse_at_users(source_raw) -> list[str]:
    """从消息 source 字段解析被 @ 人的 wxid 列表 (群聊 @mention).

    source 解出的 XML 形如:
      <msgsource><atuserlist><![CDATA[wxid_a,wxid_b]]></atuserlist>...</msgsource>
    兼容: 自闭合 <atuserlist/>、CDATA 空、裸 wxid、逗号分隔多个.
    """
    xml = _decode_source_xml(source_raw)
    if not xml or "atuserlist" not in xml:
        return []
    m = re.search(r"<atuserlist>(.*?)</atuserlist>", xml, re.S)
    if not m:
        return []
    inner = m.group(1)
    cdata = re.search(r"<!\[CDATA\[(.*?)\]\]>", inner, re.S)
    text = cdata.group(1) if cdata else inner
    return [w.strip() for w in text.split(",") if w.strip()]


# ============================================================
# 解密器
# ============================================================

class WeChatDBDecryptor:
    """微信数据库解密器 (V4 版本)"""

    def __init__(self, key_hex: str, *, direct_key: bool = False):
        if not HAS_CRYPTO:
            raise ImportError("需要 pycryptodome: uv add pycryptodome")
        self.key_hex = key_hex
        self.direct_key = direct_key

    @staticmethod
    def _parse_key(key_hex: str) -> bytes:
        key = bytes.fromhex(key_hex)
        if len(key) != KEY_SIZE:
            raise ValueError(f"密钥长度必须是 {KEY_SIZE} 字节 ({KEY_SIZE * 2} 个十六进制字符)")
        return key

    @classmethod
    def _derive_keys(cls, key: bytes, salt: bytes, direct_key: bool) -> tuple[bytes, bytes]:
        enc_key = key if direct_key else pbkdf2_hmac('sha512', key, salt, ITER_COUNT, KEY_SIZE)
        mac_salt = bytes(b ^ 0x3a for b in salt)
        mac_key = pbkdf2_hmac('sha512', enc_key, mac_salt, 2, KEY_SIZE)
        return enc_key, mac_key

    def _validate_key(self, first_page: bytes, key: bytes, direct_key: bool) -> bool:
        if len(first_page) < PAGE_SIZE:
            return False
        salt = first_page[:SALT_SIZE]
        _, mac_key = self._derive_keys(key, salt, direct_key)
        data_end = PAGE_SIZE - RESERVE_SIZE + IV_SIZE
        h = hmac.new(mac_key, digestmod=sha512)
        h.update(first_page[SALT_SIZE:data_end])
        h.update(struct.pack('<I', 1))
        return hmac.compare_digest(h.digest(), first_page[data_end:data_end + HMAC_SHA512_SIZE])

    def _decrypt_page(self, page_buf: bytes, enc_key: bytes, mac_key: bytes, page_num: int) -> bytes:
        offset = SALT_SIZE if page_num == 0 else 0
        data_end = PAGE_SIZE - RESERVE_SIZE + IV_SIZE
        h = hmac.new(mac_key, digestmod=sha512)
        h.update(page_buf[offset:data_end])
        h.update(struct.pack('<I', page_num + 1))
        if not hmac.compare_digest(h.digest(), page_buf[data_end:data_end + HMAC_SHA512_SIZE]):
            raise ValueError(f"页 {page_num} HMAC 验证失败")
        iv = page_buf[PAGE_SIZE - RESERVE_SIZE:PAGE_SIZE - RESERVE_SIZE + IV_SIZE]
        cipher = AES.new(enc_key, AES.MODE_CBC, iv)
        decrypted = cipher.decrypt(page_buf[offset:PAGE_SIZE - RESERVE_SIZE])
        return decrypted + page_buf[PAGE_SIZE - RESERVE_SIZE:]

    def decrypt_file(self, input_path: str, output_path: str) -> bool:
        """解密单个数据库文件"""
        return self._decrypt_file(input_path, output_path, self.key_hex, self.direct_key)

    def _decrypt_file(self, input_path: str, output_path: str, key_hex: str, direct_key: bool) -> bool:
        key = self._parse_key(key_hex)
        with open(input_path, 'rb') as f:
            first_page = f.read(PAGE_SIZE)

        if first_page[:len(SQLITE_HEADER) - 1] == SQLITE_HEADER[:-1]:
            print(f"  跳过 (已解密): {input_path}")
            shutil.copy2(input_path, output_path)
            return True

        if not self._validate_key(first_page, key, direct_key):
            print(f"  失败 (密钥无效): {input_path}")
            return False

        salt = first_page[:SALT_SIZE]
        enc_key, mac_key = self._derive_keys(key, salt, direct_key)
        file_size = os.path.getsize(input_path)
        total_pages = (file_size + PAGE_SIZE - 1) // PAGE_SIZE

        with open(input_path, 'rb') as fin, open(output_path, 'wb') as fout:
            fout.write(SQLITE_HEADER)
            for page_num in range(total_pages):
                page_buf = fin.read(PAGE_SIZE)
                if not page_buf:
                    break
                if len(page_buf) < PAGE_SIZE:
                    page_buf = page_buf + b'\x00' * (PAGE_SIZE - len(page_buf))
                if all(b == 0 for b in page_buf):
                    fout.write(page_buf)
                    continue
                try:
                    fout.write(self._decrypt_page(page_buf, enc_key, mac_key, page_num))
                except Exception as e:
                    print(f"  警告: 页 {page_num} 解密失败: {e}")
                    fout.write(page_buf)

        print(f"  成功: {input_path} -> {output_path}")
        return True

    def decrypt_directory(self, input_dir: str, output_dir: str) -> int:
        """解密目录下的所有数据库文件 (铺平到 output_dir 根, v1 reader 约定).

        覆盖范围:
        - message_N.db: 聊天消息表 (v1 reader 用)
        - contact.db: 联系人 (v1 reader 用)
        - message_resource.db: 图片 / 视频 server_id → md5 索引 (media resolver 用)
        - media_0.db: voice silk 字节流 (media resolver 用)
        - emoticon.db: sticker md5 → cdn_url + aes_key (media resolver fallback 用)
        """
        os.makedirs(output_dir, exist_ok=True)
        success_count = 0
        platform = get_chatlog_platform()
        db_storage_dir = platform.resolve_db_storage_dir(input_dir)
        direct_key = platform.uses_direct_db_key()

        targets = [
            ("message", re.compile(r"^message_\d*\.db$")),
            ("message", re.compile(r"^message_resource\.db$")),
            ("message", re.compile(r"^media_\d+\.db$")),
            ("contact", re.compile(r"^contact\.db$")),
            ("emoticon", re.compile(r"^emoticon\.db$")),
        ]

        for subdir, pattern in targets:
            src_dir = os.path.join(db_storage_dir, subdir)
            if not os.path.isdir(src_dir):
                continue
            for filename in os.listdir(src_dir):
                if not pattern.match(filename):
                    continue
                src = os.path.join(src_dir, filename)
                if not os.path.isfile(src):
                    continue
                key = platform.resolve_db_key(db_storage_dir, src, self.key_hex)
                if self._decrypt_file(src, os.path.join(output_dir, filename), key, direct_key):
                    success_count += 1

        return success_count

# ============================================================
# Protobuf 解析 (RoomData)
# ============================================================

def _parse_varint(data: bytes, pos: int) -> tuple[int, int]:
    """解析 varint,返回 (值, 新位置)"""
    result = 0
    shift = 0
    while pos < len(data):
        b = data[pos]
        result |= (b & 0x7f) << shift
        pos += 1
        if (b & 0x80) == 0:
            break
        shift += 7
    return result, pos


def _parse_room_members(ext_buffer: bytes) -> list[tuple[str, str]]:
    """
    解析 chat_room.ext_buffer 中的 protobuf 数据,提取全部群成员

    与 _parse_room_data 不同: 这里保留没有设置群昵称的成员(display_name 为空),
    用于获取完整成员名单.

    Returns:
        list: [(username, display_name), ...], 保持原始顺序
    """
    members: list[tuple[str, str]] = []
    if not ext_buffer or len(ext_buffer) < 2:
        return members

    pos = 0
    try:
        while pos < len(ext_buffer):
            # 读取 field tag
            tag, pos = _parse_varint(ext_buffer, pos)
            field_number = tag >> 3
            wire_type = tag & 0x07

            if wire_type == 0:  # varint
                _, pos = _parse_varint(ext_buffer, pos)
            elif wire_type == 2:  # length-delimited
                length, pos = _parse_varint(ext_buffer, pos)
                if pos + length > len(ext_buffer):
                    break

                if field_number == 1:  # users 字段
                    username, display_name = _parse_room_data_user(ext_buffer[pos:pos + length])
                    if username:
                        members.append((username, display_name))

                pos += length
            else:
                break
    except Exception:
        pass

    return members


def _parse_room_data(ext_buffer: bytes) -> dict[str, str]:
    """
    解析 chat_room.ext_buffer, 提取群成员昵称(仅含设置了群昵称的成员)

    Returns:
        dict: username -> display_name 映射
    """
    return {u: d for u, d in _parse_room_members(ext_buffer) if u and d}


def _parse_room_data_user(data: bytes) -> tuple[str, str]:
    """
    解析 RoomDataUser protobuf

    Returns:
        tuple: (username, display_name)
    """
    username = ""
    display_name = ""
    pos = 0

    try:
        while pos < len(data):
            tag, pos = _parse_varint(data, pos)
            field_number = tag >> 3
            wire_type = tag & 0x07

            if wire_type == 0:  # varint
                _, pos = _parse_varint(data, pos)
            elif wire_type == 2:  # length-delimited (string)
                length, pos = _parse_varint(data, pos)
                if pos + length > len(data):
                    break

                value = data[pos:pos + length].decode('utf-8', errors='ignore')

                if field_number == 1:  # userName
                    username = value
                elif field_number == 2:  # displayName
                    display_name = value

                pos += length
            else:
                break
    except Exception:
        pass

    return username, display_name


# ============================================================
# 读取器
# ============================================================

class WeChatDBReader:
    """微信数据库读取器"""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._contact_db: sqlite3.Connection | None = None
        self._message_dbs: dict[str, sqlite3.Connection] = {}
        self._message_db_times: list[tuple[str, int, int]] = []
        self._chatroom_display_names: dict[str, dict[str, str]] = {}  # chatroom -> {username -> display_name}

    def _find_db_files(self, pattern: str) -> list[str]:
        files = []
        regex = re.compile(pattern)
        for f in os.listdir(self.db_path):
            if regex.match(f):
                files.append(os.path.join(self.db_path, f))
        return sorted(files)

    def _get_contact_db(self) -> sqlite3.Connection:
        if self._contact_db is None:
            db_path = os.path.join(self.db_path, "contact.db")
            if not os.path.exists(db_path):
                raise FileNotFoundError(f"联系人数据库不存在: {db_path}")
            self._contact_db = sqlite3.connect(db_path)
        return self._contact_db

    def _init_message_dbs(self, strict: bool = False) -> None:
        if self._message_db_times and not strict:
            return
        db_files = self._find_db_files(r"^message_([0-9]?[0-9])?\.db$")
        infos = []
        for db_path in db_files:
            try:
                with contextlib.closing(sqlite3.connect(db_path)) as conn:
                    row = conn.execute("SELECT timestamp FROM Timestamp LIMIT 1").fetchone()
                if row:
                    infos.append((db_path, row[0]))
                else:
                    raise RuntimeError("消息数据库缺少 Timestamp")
            except Exception as e:
                if strict:
                    raise
                print(f"警告: 无法读取数据库 {db_path}: {e}")
        infos.sort(key=lambda x: x[1])
        self._message_db_times = []
        for i, (path, start_time) in enumerate(infos):
            end_time = infos[i + 1][1] if i < len(infos) - 1 else int(datetime.now().timestamp()) + 3600
            self._message_db_times.append((path, start_time, end_time))

    def _get_message_db(self, db_path: str) -> sqlite3.Connection:
        if db_path not in self._message_dbs:
            self._message_dbs[db_path] = sqlite3.connect(db_path)
        return self._message_dbs[db_path]

    def _get_dbs_for_time_range(self, start_time: int, end_time: int, strict: bool = False) -> list[str]:
        self._init_message_dbs(strict=strict)
        return [p for p, s, e in self._message_db_times if s <= end_time and e >= start_time]

    def _talker_to_table_name(self, talker: str) -> str:
        return f"Msg_{hashlib.md5(talker.encode()).hexdigest()}"

    def _decompress_content(self, content: bytes) -> str:
        if content and len(content) >= 4 and content[:4] == b'\x28\xb5\x2f\xfd' and HAS_ZSTD:
            try:
                return zstd.ZstdDecompressor().decompress(content).decode('utf-8')
            except Exception:
                pass
        return content.decode('utf-8', errors='ignore') if isinstance(content, bytes) else str(content)

    def list_groups(self, limit: int = 0) -> list[ChatRoom]:
        """列出好友和群聊"""
        db = self._get_contact_db()
        cursor = db.cursor()
        query = (
            "SELECT c.username, COALESCE(cr.owner, ''), c.nick_name, c.remark, "
            "c.small_head_url, c.big_head_url "
            "FROM contact c "
            "LEFT JOIN chat_room cr ON c.username = cr.username "
            "WHERE ((c.local_type = 1 AND (c.flag & 2) != 0) "
            "OR (c.local_type = 2 AND c.is_in_chat_room = 1) "
            "OR c.local_type = 5) "
            "AND c.username NOT LIKE 'gh_%' "
            "AND c.username NOT IN "
            "('filehelper','fmessage','floatbottle','medianote','newsapp','weixin','weixinpay') "
            "AND c.delete_flag = 0 "
            "ORDER BY c.local_type, c.remark, c.nick_name"
        )
        if limit > 0:
            query += " LIMIT ?"
            cursor.execute(query, (limit,))
        else:
            cursor.execute(query)
        return [
            ChatRoom(
                username=row[0] or "",
                owner=row[1] or "",
                nick_name=row[2] or "",
                remark=row[3] or "",
                avatar=row[4] or "",
                big_avatar=row[5] or "",
            )
            for row in cursor.fetchall()
        ]

    def get_group_display_name(self, group_id: str) -> str:
        """获取群聊显示名称(优先 remark,其次 nick_name)"""
        db = self._get_contact_db()
        cursor = db.cursor()
        cursor.execute("SELECT nick_name, remark FROM contact WHERE username = ?", (group_id,))
        row = cursor.fetchone()
        if row:
            return row[1] or row[0] or group_id
        return group_id

    def get_contact_name(self, username: str) -> str:
        """获取联系人名称"""
        db = self._get_contact_db()
        cursor = db.cursor()
        cursor.execute("SELECT nick_name, remark, alias FROM contact WHERE username = ?", (username,))
        row = cursor.fetchone()
        if row:
            return row[1] or row[0] or row[2] or username
        return username

    def get_contacts(self, usernames: list[str]) -> list[Contact]:
        """批量查询联系人/群成员信息(含头像 URL)"""
        if not usernames:
            return []
        db = self._get_contact_db()
        cursor = db.cursor()
        placeholders = ",".join("?" * len(usernames))
        cursor.execute(
            f"SELECT username, nick_name, remark, alias, small_head_url, big_head_url "
            f"FROM contact WHERE username IN ({placeholders})",
            list(usernames),
        )
        return [
            Contact(
                username=r[0] or "",
                nick_name=r[1] or "",
                remark=r[2] or "",
                alias=r[3] or "",
                avatar=r[4] or "",
                big_avatar=r[5] or "",
            )
            for r in cursor.fetchall()
        ]

    def get_room_members(self, chatroom: str) -> list[RoomMember]:
        """列出群成员: 群内昵称 + 联系人昵称/头像, 群主排首位"""
        db = self._get_contact_db()
        cursor = db.cursor()
        cursor.execute("SELECT owner, ext_buffer FROM chat_room WHERE username = ?", (chatroom,))
        row = cursor.fetchone()
        if not row or not row[1]:
            return []
        owner = row[0] or ""
        members = _parse_room_members(row[1])
        if not members:
            return []

        display_map = {wxid: name for wxid, name in members}
        contacts = {c.username: c for c in self.get_contacts(list(display_map.keys()))}
        result = [
            _build_room_member(wxid, name, contacts.get(wxid), wxid == owner)
            for wxid, name in display_map.items()
        ]
        result.sort(key=lambda m: not m.is_owner)
        return result

    def _get_chatroom_display_names(self, chatroom: str) -> dict[str, str]:
        """获取群成员的群昵称映射"""
        if chatroom in self._chatroom_display_names:
            return self._chatroom_display_names[chatroom]

        display_names = {}
        try:
            db = self._get_contact_db()
            cursor = db.cursor()
            cursor.execute("SELECT ext_buffer FROM chat_room WHERE username = ?", (chatroom,))
            row = cursor.fetchone()
            if row and row[0]:
                display_names = _parse_room_data(row[0])
        except Exception:
            pass

        self._chatroom_display_names[chatroom] = display_names
        return display_names

    def get_messages(
        self,
        talker: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        text_only: bool = True,
        limit: int = 0,
        offset: int = 0,
        desc: bool = False,
    ) -> list[Message]:
        return self._get_messages(
            talker=talker,
            start_time=start_time,
            end_time=end_time,
            text_only=text_only,
            limit=limit,
            offset=offset,
            desc=desc,
        )

    def search_messages(
        self,
        talker: str,
        *,
        query: str,
        senders: list[str],
        msg_types: list[int] | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        limit: int = 100,
        offset: int = 0,
        desc: bool = False,
    ) -> list[Message]:
        return self._get_messages(
            talker=talker,
            start_time=start_time,
            end_time=end_time,
            text_only=False,
            msg_types=[1] if msg_types is None else msg_types,
            limit=limit,
            offset=offset,
            desc=desc,
            senders=senders,
            content_query=query,
        )

    def count_messages(
        self,
        talker: str,
        *,
        query: str,
        senders: list[str],
        msg_types: list[int] | None = None,
        bucket: str | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
    ) -> ChatlogMessageCountResponse:
        return self._get_messages(
            talker=talker,
            start_time=start_time,
            bucket=bucket,
            end_time=end_time,
            text_only=False,
            msg_types=[1] if msg_types is None else msg_types,
            count_only=True,
            senders=senders,
            content_query=query,
        )

    def _get_messages(
        self,
        talker: str,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        text_only: bool = True,
        limit: int = 0,
        offset: int = 0,
        desc: bool = False,
        senders: list[str] | None = None,
        content_query: str = "",
        msg_types: list[int] | None = None,
        count_only: bool = False,
        bucket: str | None = None,
    ) -> list[Message] | ChatlogMessageCountResponse:
        """获取聊天记录"""
        start_time = start_time or datetime(2000, 1, 1)
        end_time = end_time or datetime.now()
        start_ts, end_ts = int(start_time.timestamp()), int(end_time.timestamp())

        if bucket not in (None, "hour", "day"):
            raise ValueError("bucket 仅支持 hour 或 day")
        buckets: dict[str, dict[str, int]] | None = {} if bucket else None
        db_paths = self._get_dbs_for_time_range(start_ts, end_ts, strict=count_only)
        if not db_paths:
            return build_message_count({}, buckets, bucket) if count_only else []

        table_name = self._talker_to_table_name(talker)
        if not re.fullmatch(r'Msg_[0-9a-f]{32}', table_name):
            raise ValueError(f"Invalid table name derived from talker: {talker}")
        is_chatroom = talker.endswith("@chatroom")
        messages = []

        # 获取群昵称映射
        display_names = self._get_chatroom_display_names(talker) if is_chatroom and not count_only else {}
        by_sender: dict[str, int] = {}
        search_sender, search_content = message_search_expressions(is_chatroom)
        compressed = "hex(substr(m.message_content, 1, 4)) = '28B52FFD'"
        sender_content = f"CASE WHEN {compressed} THEN m.message_content ELSE NULL END" if is_chatroom else "NULL"
        bucket_format = "%Y-%m-%dT%H:00:00" if bucket == "hour" else "%Y-%m-%dT00:00:00"
        bucket_start = f"strftime('{bucket_format}', m.create_time, 'unixepoch', 'localtime')" if bucket else "NULL"

        # 单 db 内 sort_seq 单调,最多需要 offset + limit 条;合并排序后再切片
        per_db_cap = (offset + limit) if limit > 0 and not (senders and is_chatroom) else 0

        for db_path in db_paths:
            db = self._get_message_db(db_path)
            cursor = db.cursor()
            cursor.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
            if not cursor.fetchone():
                continue

            columns = f"{search_sender}, {sender_content} AS sender_content, {bucket_start} AS bucket_start, COUNT(*)" if count_only else (
                "m.sort_seq, m.server_id, m.local_type, n.user_name, m.local_id, "
                "m.create_time, m.message_content, m.status, m.source"
            )
            query = f"""
                SELECT {columns}
                FROM {table_name} m
                LEFT JOIN Name2Id n ON m.real_sender_id = n.rowid
                WHERE m.create_time >= ? AND m.create_time <= ?
            """
            params: list = [start_ts, end_ts]
            if text_only:
                query += " AND (m.local_type & 0xFFFFFFFF) = ?"
                params.append(MESSAGE_TYPE_TEXT)
            if msg_types:
                placeholders = ",".join("?" for _ in msg_types)
                query += f" AND (m.local_type & 0xFFFFFFFF) IN ({placeholders})"
                params.extend(msg_types)
            if content_query:
                query += " AND (m.local_type & 0xFFFFFFFF) = ?"
                params.append(MESSAGE_TYPE_TEXT)
            if senders:
                placeholders = ",".join("?" for _ in senders)
                query += f" AND ({search_sender} IN ({placeholders})"
                query += f" OR {compressed})" if is_chatroom else ")"
                params.extend(senders)
            if content_query:
                query += (
                    " AND hex(substr(m.message_content, 1, 4)) != '28B52FFD'"
                    f" AND {search_content} LIKE ? ESCAPE '\\' COLLATE NOCASE"
                )
                params.append(f"%{escape_like_query(content_query)}%")
            if count_only:
                query += f" GROUP BY {search_sender}, sender_content, bucket_start"
                cursor.execute(query, params)
                for sender, content, start, count in cursor.fetchall():
                    if content is not None:
                        sender = resolve_message_sender(sender, self._decompress_content(content))
                    if senders and sender not in senders:
                        continue
                    by_sender[sender] = by_sender.get(sender, 0) + count
                    if buckets is not None:
                        counts = buckets.setdefault(start, {})
                        counts[sender] = counts.get(sender, 0) + int(count)
                continue
            query += f" ORDER BY m.sort_seq {'DESC' if desc else 'ASC'}"
            if per_db_cap > 0:
                query += " LIMIT ?"
                params.append(per_db_cap)
            cursor.execute(query, params)

            for sort_seq, server_id, local_type, user_name, local_id, create_time, message_content, status, source in cursor.fetchall():
                msg_type = local_type & 0xFFFFFFFF
                if text_only and msg_type != MESSAGE_TYPE_TEXT:
                    continue

                content = self._decompress_content(message_content) if message_content else ""
                at_users = _parse_at_users(source) if is_chatroom else []
                sender = user_name or ""

                if is_chatroom and ":\n" in content:
                    parts = content.split(":\n", 1)
                    if len(parts) == 2 and " " not in parts[0] and "\n" not in parts[0]:
                        sender, content = parts

                if senders and sender not in senders:
                    continue

                # voip (msg_type=50) status 恒为 2 不可靠, 退化纯 sender 判
                is_self = (
                    (not is_chatroom and talker != sender) if msg_type == 50
                    else (status == 2 or (not is_chatroom and talker != sender))
                )
                # 优先使用群昵称,其次使用联系人名称
                sender_name = display_names.get(sender) or self.get_contact_name(sender) if sender else ""

                messages.append(Message(
                    seq=sort_seq,
                    time=datetime.fromtimestamp(create_time),
                    talker=talker,
                    sender=sender,
                    sender_name=sender_name,
                    msg_type=msg_type,
                    content=content,
                    is_self=is_self,
                    server_id=int(server_id) if server_id is not None else 0,
                    local_id=int(local_id) if local_id is not None else 0,
                    at_users=at_users,
                ))

        if count_only:
            return build_message_count(by_sender, buckets, bucket)

        messages.sort(key=lambda m: m.seq, reverse=desc)
        if offset > 0:
            messages = messages[offset:]
        if limit > 0:
            messages = messages[:limit]
        return messages

    def close(self) -> None:
        if self._contact_db:
            self._contact_db.close()
        for conn in self._message_dbs.values():
            conn.close()


# ============================================================
# CLI 命令
# ============================================================

def cmd_decrypt(args) -> int:
    """解密命令"""
    if not HAS_CRYPTO:
        print("缺少依赖: uv add pycryptodome")
        return 1
    if not os.path.isdir(args.input):
        print(f"错误: 目录不存在: {args.input}")
        return 1

    print("开始解密...")
    print(f"输入: {args.input}")
    print(f"输出: {args.output}\n")

    try:
        decryptor = WeChatDBDecryptor(args.key)
        count = decryptor.decrypt_directory(args.input, args.output)
        print(f"\n完成! 成功解密 {count} 个文件")
        return 0
    except Exception as e:
        print(f"解密失败: {e}")
        return 1


def cmd_list_groups(args) -> int:
    """列出群聊命令"""
    if not os.path.isdir(args.db_path):
        print(f"错误: 目录不存在: {args.db_path}")
        return 1

    reader = WeChatDBReader(args.db_path)
    try:
        groups = reader.list_groups(limit=args.limit)
        if not groups:
            print("没有找到群聊")
            return 0

        if args.json:
            print(json.dumps([g.to_dict() for g in groups], ensure_ascii=False, indent=2))
        else:
            print(f"共找到 {len(groups)} 个群聊:\n")
            for i, g in enumerate(groups, 1):
                display = g.display_name()
                if display != g.username:
                    print(f"{i:4}. {display} ({g.username})")
                else:
                    print(f"{i:4}. {g.username}")
        return 0
    finally:
        reader.close()


def cmd_query(args) -> int:
    """查询消息命令"""
    if not os.path.isdir(args.db_path):
        print(f"错误: 目录不存在: {args.db_path}")
        return 1

    start_time = end_time = None
    try:
        if args.start:
            fmt = "%Y-%m-%d %H:%M:%S" if " " in args.start else "%Y-%m-%d"
            start_time = datetime.strptime(args.start, fmt)
        if args.end:
            fmt = "%Y-%m-%d %H:%M:%S" if " " in args.end else "%Y-%m-%d"
            end_time = datetime.strptime(args.end, fmt)
            if " " not in args.end:
                end_time = end_time.replace(hour=23, minute=59, second=59)
    except ValueError as e:
        print(f"错误: 无效的时间格式: {e}")
        return 1

    reader = WeChatDBReader(args.db_path)
    try:
        messages = reader.get_messages(
            talker=args.group,
            start_time=start_time,
            end_time=end_time,
            text_only=True,
            limit=args.limit
        )

        if not messages:
            print("没有找到消息")
            return 0

        if args.json:
            print(json.dumps([m.to_dict() for m in messages], ensure_ascii=False, indent=2))
        else:
            print(f"共 {len(messages)} 条消息:\n")
            for msg in messages:
                print(msg.format())

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                if args.json:
                    json.dump([m.to_dict() for m in messages], f, ensure_ascii=False, indent=2)
                else:
                    for msg in messages:
                        f.write(msg.format() + "\n")
            print(f"\n已保存到: {args.output}")

        return 0
    finally:
        reader.close()


def main():
    parser = argparse.ArgumentParser(
        description="微信聊天记录工具",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # decrypt 命令
    p_decrypt = subparsers.add_parser("decrypt", help="解密微信数据库")
    p_decrypt.add_argument("--input", "-i", required=True, help="加密数据库目录 (DataDir)")
    p_decrypt.add_argument("--key", "-k", required=True, help="32字节密钥的十六进制字符串")
    p_decrypt.add_argument("--output", "-o", required=True, help="解密输出目录")

    # list-groups 命令
    p_list = subparsers.add_parser("list-groups", help="列出所有群聊")
    p_list.add_argument("--db-path", "-d", required=True, help="解密后的数据库目录")
    p_list.add_argument("--limit", "-n", type=int, default=0, help="限制返回数量")
    p_list.add_argument("--json", "-j", action="store_true", help="输出 JSON 格式")

    # query 命令
    p_query = subparsers.add_parser("query", help="查询聊天记录")
    p_query.add_argument("--db-path", "-d", required=True, help="解密后的数据库目录")
    p_query.add_argument("--group", "-g", required=True, help="群聊ID 或个人微信ID")
    p_query.add_argument("--start", "-s", help="开始时间 (YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS)")
    p_query.add_argument("--end", "-e", help="结束时间 (YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS)")
    p_query.add_argument("--limit", "-n", type=int, default=0, help="限制返回数量")
    p_query.add_argument("--output", "-o", help="输出到文件")
    p_query.add_argument("--json", "-j", action="store_true", help="输出 JSON 格式")

    args = parser.parse_args()

    if args.command == "decrypt":
        return cmd_decrypt(args)
    elif args.command == "list-groups":
        return cmd_list_groups(args)
    elif args.command == "query":
        return cmd_query(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    exit(main())
