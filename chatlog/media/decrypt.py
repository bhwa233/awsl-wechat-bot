"""微信 4.x V2 .dat 解密 (仅 V2 格式).

V2 文件结构:
  [6B signature: 07 08 V2 08 07] [4B aes_size LE] [4B xor_size LE] [1B padding]
  [aligned_aes_size bytes AES-128-ECB] [raw_data] [xor_size bytes XOR]

算法来自 https://github.com/ylytdeng/wechat-decrypt (decode_image.py),
精简到仅 V2 支持: 旧 XOR / V1 固定 key 路径全部移除.
"""

from __future__ import annotations

import struct
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Util import Padding

V2_MAGIC = b"\x07\x08V2\x08\x07"


def is_v2_format(dat_path: str | Path) -> bool:
    try:
        with open(dat_path, "rb") as f:
            return f.read(6) == V2_MAGIC
    except OSError:
        return False


def _aligned_aes_block_size(aes_size: int) -> int:
    """AES-CBC + PKCS7 padding 块对齐.

    - aes_size 非 16 倍数: 向上对齐到 16 (aes_size + (16 - aes_size%16))
    - aes_size 是 16 倍数: 完整 padding 块 (aes_size + 16)
    """
    if aes_size % 16:
        return aes_size + (16 - aes_size % 16)
    return aes_size + 16


def detect_image_format(header_bytes: bytes) -> str:
    """从解密后的文件头识别格式, 返回 jpg/png/gif/bmp/webp/tif/hevc/bin."""
    if header_bytes[:3] == b"\xff\xd8\xff":
        return "jpg"
    if header_bytes[:4] == b"\x89PNG":
        return "png"
    if header_bytes[:3] == b"GIF":
        return "gif"
    if header_bytes[:2] == b"BM":
        return "bmp"
    if header_bytes[:4] == b"RIFF" and len(header_bytes) >= 12 and header_bytes[8:12] == b"WEBP":
        return "webp"
    if header_bytes[:4] == b"II*\x00":
        return "tif"
    if header_bytes[:4] == b"wxgf":
        return "hevc"
    return "bin"


def decrypt_v2_dat(
    dat_path: str | Path,
    aes_key: str | bytes,
    xor_key: int = 0x88,
) -> tuple[bytes, str] | None:
    """解密 V2 .dat 文件, 返回 (明文 bytes, 格式扩展名) 或 None.

    Args:
        dat_path: V2 .dat 文件路径
        aes_key: 16 字节 AES key (str 或 bytes)
        xor_key: XOR 字节 (默认 0x88)

    Returns:
        (data, fmt) 或 None (非 V2 / AES 失败 / 格式不识别 / XOR 校验失败)
    """
    if isinstance(aes_key, str):
        aes_key = aes_key.encode("ascii")
    if len(aes_key) < 16:
        return None
    aes_key = aes_key[:16]

    with open(dat_path, "rb") as f:
        data = f.read()

    if len(data) < 15 or data[:6] != V2_MAGIC:
        return None

    aes_size, xor_size = struct.unpack_from("<LL", data, 6)
    aligned = _aligned_aes_block_size(aes_size)

    offset = 15
    if offset + aligned > len(data):
        return None

    try:
        cipher = AES.new(aes_key, AES.MODE_ECB)
        dec_aes = Padding.unpad(cipher.decrypt(data[offset:offset + aligned]), AES.block_size)
    except (ValueError, KeyError):
        return None
    offset += aligned

    raw_end = len(data) - xor_size
    raw_data = data[offset:raw_end] if offset < raw_end else b""
    xor_data = data[raw_end:]
    dec_xor = bytes(b ^ xor_key for b in xor_data)

    plain = dec_aes + raw_data + dec_xor
    fmt = detect_image_format(plain[:16])

    # AES key 错时通常解出乱码, detect_image_format 返回 'bin' — 拒绝
    if fmt == "bin":
        return None

    # XOR key 错时 AES/raw 段可能合法 (看似正常 magic), 但 XOR 段乱码.
    # 用尾部 magic 验证 XOR key 正确性 (仅 JPG/PNG 有强制 trailer signature).
    if xor_size >= 2:
        if fmt == "jpg" and plain[-2:] != b"\xff\xd9":
            return None
        if fmt == "png" and b"IEND" not in plain[-12:]:
            return None

    return plain, fmt
