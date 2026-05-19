#!/usr/bin/env python3
"""
Windows-only WeChat Image AES Key Extractor (derivation, no process read required)

通过微信 4.x 在磁盘上的命名约定派生出 V2 .dat 图片解密所需的
(xor_key, aes_key)。**不读进程内存, 不需要管理员**.

派生算法 (与 macOS find_image_key_macos.py 方案2 同源, 跨平台通用)
------------------------------------------------------------------
- xor_key = uin & 0xFF                                     # 1 字节
- aes_key = MD5(str(uin) + wxid_norm).hexdigest()[:16]     # 16 字节 ASCII
- wxid 目录后 4 位 hex == md5(str(uin))[:4]                # 缩小候选

V2 .dat 结构:
  [6B signature: 07 08 V2 08 07] [4B aes_size LE] [4B xor_size LE] [1B padding]
  [aligned_aes_size bytes AES-ECB] [raw_data] [xor_size bytes XOR]

流程:
  1. 自动定位 %USERPROFILE%\\Documents\\xwechat_files\\<wxid>_<4hex>
  2. wxid 归一化, 解析后缀 4 位 hex
  3. 从最近的 V2 _t.dat 末两字节投票推导 xor_key (JPG FF D9)
  4. 收集 16B AES 密文模板 (多个不同样本)
  5. 多进程枚举 (uin & 0xff == xor_key) 的 2^24 候选,
     找 md5(uin)[:4] == wxid_suffix, 再用 AES 模板验证唯一定位

依赖:
  uv run --with pycryptodome tools/extract_wx_image_key_windows.py

参考: https://github.com/ylytdeng/wechat-decrypt (find_image_key_macos.py 方案2)
致谢: @H3CoF6 在 issue #68 提供的 "wxid 后 4 位 == md5(uin)[:4]" 关键洞察
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import multiprocessing
import os
import queue as _queue
import re
import sys
import time
from collections import Counter
from pathlib import Path

V2_MAGIC = b"\x07\x08V2\x08\x07"

IMAGE_MAGICS = (
    b"\xff\xd8\xff",          # JPEG
    b"\x89\x50\x4e\x47",      # PNG
    b"GIF",                   # GIF
    b"RIFF",                  # WebP
    b"wxgf",                  # 微信 HEVC / Live Photo
)

_WXID_HEX_SUFFIX_RE = re.compile(r"^(.+)_([0-9a-fA-F]{4})$")


def require_windows() -> None:
    if sys.platform != "win32":
        print(f"错误: 仅支持 Windows, 当前平台: {sys.platform}", file=sys.stderr)
        sys.exit(1)


def require_pycryptodome():
    try:
        from Crypto.Cipher import AES  # noqa: F401
    except ImportError:
        print(
            "请安装 pycryptodome: uv run --with pycryptodome tools/extract_wx_image_key_windows.py",
            file=sys.stderr,
        )
        sys.exit(1)


def auto_detect_wxid_dir() -> Path | None:
    """定位 Documents\\xwechat_files\\<wxid>_<4hex>, 取最近修改的."""
    home = Path.home()
    candidates: list[Path] = []
    for root in (
        home / "Documents" / "xwechat_files",
        home / "xwechat_files",
    ):
        if not root.is_dir():
            continue
        for child in root.iterdir():
            if not child.is_dir():
                continue
            if (child / "msg" / "attach").is_dir():
                candidates.append(child)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def parse_wxid_suffix(wxid_dir: Path) -> tuple[str, str] | None:
    name = wxid_dir.name
    m = _WXID_HEX_SUFFIX_RE.match(name)
    if not m:
        return None
    return m.group(1), m.group(2).lower()


def collect_v2_samples(attach_dir: Path, limit: int = 32) -> list[tuple[Path, bytes, bytes]]:
    pattern = str(attach_dir / "*" / "*" / "Img" / "*_t.dat")
    files = sorted(glob.glob(pattern), key=os.path.getmtime, reverse=True)
    samples: list[tuple[Path, bytes, bytes]] = []
    for f in files[:limit]:
        try:
            size = os.path.getsize(f)
            with open(f, "rb") as fp:
                header = fp.read(31)
                if header[:6] != V2_MAGIC or len(header) < 31:
                    continue
                fp.seek(size - 2)
                tail = fp.read(2)
            if len(tail) != 2:
                continue
            samples.append((Path(f), header[15:31], tail))
        except OSError:
            continue
    return samples


def derive_xor_key(samples: list[tuple[Path, bytes, bytes]]) -> int | None:
    if not samples:
        return None
    last_bytes = [tail[-1] ^ 0xD9 for _, _, tail in samples]
    top, votes = Counter(last_bytes).most_common(1)[0]
    if votes < max(2, len(samples) // 3):
        return None
    return top


def _bruteforce_worker(
    start: int,
    end: int,
    xor_key: int,
    suffix_bytes: bytes,
    wxid_bytes: bytes,
    templates: tuple[bytes, ...],
    result_queue,
):
    from Crypto.Cipher import AES

    def _match(aes_bytes: bytes, ct: bytes) -> bool:
        try:
            dec = AES.new(aes_bytes, AES.MODE_ECB).decrypt(ct)
        except (ValueError, KeyError):
            return False
        return any(dec.startswith(m) for m in IMAGE_MAGICS)

    for i in range(start, end):
        uin = (i << 8) | xor_key
        uin_bytes = str(uin).encode("ascii")
        if hashlib.md5(uin_bytes).digest()[:2] != suffix_bytes:
            continue
        aes_hex = hashlib.md5(uin_bytes + wxid_bytes).hexdigest()[:16]
        aes_bytes = aes_hex.encode("ascii")
        if all(_match(aes_bytes, ct) for ct in templates):
            result_queue.put((uin, aes_hex))
            return


def bruteforce_uin(
    xor_key: int,
    suffix_hex: str,
    wxid_norm: str,
    templates: tuple[bytes, ...],
    workers: int | None = None,
    timeout: float = 120.0,
) -> tuple[int, str] | None:
    suffix_bytes = bytes.fromhex(suffix_hex)
    wxid_bytes = wxid_norm.encode("ascii")
    if workers is None:
        workers = max(1, multiprocessing.cpu_count())
    total = 1 << 24
    chunk = total // workers

    queue = multiprocessing.Queue()
    procs = []
    for i in range(workers):
        s = i * chunk
        e = (i + 1) * chunk if i != workers - 1 else total
        p = multiprocessing.Process(
            target=_bruteforce_worker,
            args=(s, e, xor_key, suffix_bytes, wxid_bytes, templates, queue),
            daemon=True,
        )
        p.start()
        procs.append(p)

    found: tuple[int, str] | None = None
    deadline = time.time() + timeout
    try:
        while any(p.is_alive() for p in procs) and time.time() < deadline:
            try:
                found = queue.get(timeout=0.2)
                break
            except _queue.Empty:
                continue
        if not found:
            try:
                found = queue.get_nowait()
            except _queue.Empty:
                pass
    finally:
        for p in procs:
            if p.is_alive():
                p.terminate()
        for p in procs:
            p.join(timeout=1)
    return found


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Windows: 派生微信图片 V2 .dat AES key (无需读进程内存)",
    )
    parser.add_argument("--wxid-dir", type=Path, help="xwechat_files\\<wxid>_<hex> 目录")
    parser.add_argument("--xor-key", type=lambda s: int(s, 0), help="手动指定 XOR key (0~255)")
    parser.add_argument("--workers", type=int, default=None, help="并发进程数, 默认 CPU 核数")
    parser.add_argument("--timeout", type=float, default=120.0, help="超时秒数, 默认 120")
    return parser.parse_args()


def main() -> int:
    require_windows()
    require_pycryptodome()
    args = parse_args()

    wxid_dir = args.wxid_dir or auto_detect_wxid_dir()
    if not wxid_dir or not wxid_dir.is_dir():
        print("错误: 未能定位 xwechat_files\\<wxid> 目录, 请用 --wxid-dir 指定", file=sys.stderr)
        return 1
    print(f"wxid 目录: {wxid_dir}")

    parsed = parse_wxid_suffix(wxid_dir)
    if not parsed:
        print(
            f"错误: 目录名 {wxid_dir.name} 不符合 <wxid>_<4hex> 约定, 无法派生",
            file=sys.stderr,
        )
        return 1
    wxid_norm, suffix_hex = parsed
    print(f"wxid 归一化: {wxid_norm}, 后缀: {suffix_hex}")

    attach_dir = wxid_dir / "msg" / "attach"
    if not attach_dir.is_dir():
        print(f"错误: attach 目录不存在: {attach_dir}", file=sys.stderr)
        return 1

    samples = collect_v2_samples(attach_dir)
    if not samples:
        print("错误: 未找到 V2 格式 _t.dat 样本", file=sys.stderr)
        return 1
    print(f"V2 样本: {len(samples)} 个")

    if args.xor_key is not None:
        xor_key = args.xor_key & 0xFF
        print(f"使用用户指定 XOR key: 0x{xor_key:02x}")
    else:
        xor_key = derive_xor_key(samples)
        if xor_key is None:
            print(
                "错误: 末字节投票分歧过大, 无法推导 XOR key, 请用 --xor-key 手动指定",
                file=sys.stderr,
            )
            return 1
        print(f"推导 XOR key: 0x{xor_key:02x} (uin & 0xff)")

    templates = tuple(ct for _, ct, _ in samples[:6])
    print(f"AES 模板: {len(templates)} 个")

    workers = args.workers or max(1, multiprocessing.cpu_count())
    print(f"并行 worker: {workers}, 候选 2^24, 开始爆破...")

    t0 = time.time()
    result = bruteforce_uin(
        xor_key=xor_key,
        suffix_hex=suffix_hex,
        wxid_norm=wxid_norm,
        templates=templates,
        workers=workers,
        timeout=args.timeout,
    )
    elapsed = time.time() - t0
    print(f"完成 ({elapsed:.1f}s)")

    if not result:
        print("\n未找到匹配的 uin/aes_key. 可能原因:", file=sys.stderr)
        print("  - XOR key 推导错误 (样本非全 JPEG), 试 --xor-key 手动指定", file=sys.stderr)
        print("  - wxid 归一化不匹配, 微信版本与算法不兼容", file=sys.stderr)
        return 1

    uin, aes_key = result
    print(f"\n=== 结果 ===")
    print(f"uin = {uin}")
    print(f"IMAGE_AES_KEY={aes_key}")
    print(f"IMAGE_XOR_KEY=0x{xor_key:02x}")
    print("\n请将以上两行写入 .env")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
