#!/usr/bin/env python3
"""
Linux-only WeChat SQLCipher key extractor.

It scans the memory of a running Linux WeChat process for SQLCipher key records
and matches the embedded salts against local WeChat .db files.

Run as root, for example:

    sudo uv run tools/extract_wx_keys_linux.py --json
    sudo uv run tools/extract_wx_keys_linux.py -o ~/.awsl-wechat-bot/wx_keys.json
    sudo uv run tools/extract_wx_keys_linux.py --format detail --json
    sudo uv run tools/extract_wx_keys_linux.py --db-dir /path/to/db_storage --json

The default JSON format can be used directly as WECHAT_DB_KEY on Linux:

    {"contact/contact.db": "64hex", "message/message_0.db": "64hex"}
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import pwd
import re
import stat
import sys
from pathlib import Path
from typing import BinaryIO

PATTERN_RE = re.compile(rb"x'([0-9a-fA-F]{96})'")
HEX_PATTERN_LEN = 96
PATTERN_TOTAL_LEN = HEX_PATTERN_LEN + 3
CHUNK_SIZE = 2 * 1024 * 1024
OVERLAP_SIZE = PATTERN_TOTAL_LEN
SQLITE_HEADER_PREFIX = b"SQLite format 3"


def main() -> int:
    args = parse_args()

    if not is_linux():
        system = platform.system() or sys.platform
        print(
            f"错误: extract_wx_keys_linux.py 仅支持 Linux, 当前平台: {system}",
            file=sys.stderr,
        )
        return 1

    db_dir = args.db_dir or auto_detect_db_dir()
    if not db_dir:
        print("错误: 未能自动检测 db_storage, 请用 --db-dir 指定", file=sys.stderr)
        return 1
    if not db_dir.is_dir():
        print(f"错误: db_storage 不存在: {db_dir}", file=sys.stderr)
        return 1

    pid = args.pid or find_wechat_pid(args.process)
    if not pid:
        names = ", ".join(args.process)
        print(f"错误: 找不到微信进程, 已尝试: {names}", file=sys.stderr)
        return 1

    print(f"微信 PID: {pid}", file=sys.stderr)
    print(f"数据目录: {db_dir}", file=sys.stderr)

    db_salts = collect_db_salts(db_dir)
    print(f"找到 {len(db_salts)} 个加密数据库", file=sys.stderr)
    if not db_salts:
        print("错误: 没有找到可匹配的加密 .db 文件", file=sys.stderr)
        return 1

    try:
        raw_keys = scan_process_keys(pid)
    except PermissionError:
        print(f"错误: 无法读取 /proc/{pid}/mem, 请使用 sudo 运行", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"错误: 扫描进程内存失败: {exc}", file=sys.stderr)
        return 1

    print(f"找到 {len(raw_keys)} 个候选密钥", file=sys.stderr)
    matched = match_keys(db_salts, raw_keys)
    print(f"匹配到 {len(matched)} 个数据库密钥", file=sys.stderr)

    result = format_result(matched, args.output_format)

    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        write_output(args.output, text)
        print(f"密钥已保存: {args.output}", file=sys.stderr)
        return 0

    print(text)
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Linux-only: extract local WeChat SQLCipher keys."
    )
    parser.add_argument(
        "--db-dir",
        type=Path,
        help="WeChat db_storage path. Auto-detected when omitted.",
    )
    parser.add_argument(
        "--pid",
        type=int,
        help="WeChat process PID. Auto-detected when omitted.",
    )
    parser.add_argument(
        "--process",
        nargs="+",
        default=["wechat", "weixin"],
        help="Process names to match in /proc/<pid>/comm.",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="Write JSON to file instead of stdout. File mode is set to 0600.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Compatibility flag; output is always JSON.",
    )
    parser.add_argument(
        "--format",
        dest="output_format",
        choices=["flat", "detail"],
        default="flat",
        help="JSON format. flat maps db path to enc_key. detail also includes salt.",
    )
    return parser.parse_args()


def is_linux() -> bool:
    return sys.platform.startswith("linux")


def auto_detect_db_dir() -> Path | None:
    homes = [Path.home()]
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user:
        try:
            homes.append(Path(pwd.getpwnam(sudo_user).pw_dir))
        except KeyError:
            homes.append(Path("/home") / sudo_user)

    candidates: list[Path] = []
    for home in dict.fromkeys(homes):
        for xwechat in (
            home / "xwechat_files",
            home / "Documents" / "xwechat_files",
        ):
            if xwechat.exists():
                for child in xwechat.iterdir():
                    storage = child / "db_storage"
                    if storage.is_dir():
                        candidates.append(storage)

        old_storage = home / ".local" / "share" / "weixin" / "data" / "db_storage"
        if old_storage.is_dir():
            candidates.append(old_storage)

    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def find_wechat_pid(process_names: list[str]) -> int | None:
    wanted = {name.lower() for name in process_names}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue

        comm_path = proc / "comm"
        try:
            comm = comm_path.read_text(encoding="utf-8", errors="ignore").strip().lower()
        except OSError:
            continue

        if comm in wanted:
            return int(proc.name)

    return None


def collect_db_salts(db_dir: Path) -> dict[str, list[str]]:
    salts: dict[str, list[str]] = {}
    for path in db_dir.rglob("*.db"):
        salt = read_db_salt(path)
        if not salt:
            continue

        rel = path.relative_to(db_dir).as_posix()
        salts.setdefault(salt, []).append(rel)

    return salts


def read_db_salt(path: Path) -> str | None:
    try:
        with path.open("rb") as file:
            header = file.read(16)
    except OSError:
        return None

    if len(header) < 16:
        return None
    if header[:15] == SQLITE_HEADER_PREFIX:
        return None
    return header.hex()


def scan_process_keys(pid: int) -> list[tuple[str, str]]:
    regions = parse_maps(pid)
    results: set[tuple[str, str]] = set()
    mem_path = Path(f"/proc/{pid}/mem")

    with mem_path.open("rb", buffering=0) as mem:
        for start, end in regions:
            scan_region(mem, start, end, results)

    return sorted(results)


def parse_maps(pid: int) -> list[tuple[int, int]]:
    maps_path = Path(f"/proc/{pid}/maps")
    regions: list[tuple[int, int]] = []

    with maps_path.open("r", encoding="utf-8", errors="ignore") as file:
        for line in file:
            parts = line.split(maxsplit=2)
            if len(parts) < 2:
                continue

            addr_range, perms = parts[0], parts[1]
            if not perms.startswith("rw"):
                continue

            start_s, end_s = addr_range.split("-", maxsplit=1)
            regions.append((int(start_s, 16), int(end_s, 16)))

    return regions


def scan_region(
    mem: BinaryIO,
    start: int,
    end: int,
    results: set[tuple[str, str]],
) -> None:
    offset = 0
    total_len = end - start
    tail = b""

    while offset < total_len:
        size = min(CHUNK_SIZE, total_len - offset)
        try:
            mem.seek(start + offset)
            chunk = mem.read(size)
        except OSError:
            return

        if not chunk:
            return

        search_buffer = tail + chunk
        for match in PATTERN_RE.finditer(search_buffer):
            raw = match.group(1).decode("ascii").lower()
            results.add((raw[:64], raw[64:96]))

        tail = search_buffer[-OVERLAP_SIZE:]
        offset += size


def match_keys(
    db_salts: dict[str, list[str]],
    raw_keys: list[tuple[str, str]],
) -> list[dict[str, str]]:
    matched: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()

    for enc_key, salt in raw_keys:
        db_names = db_salts.get(salt)
        if not db_names:
            continue

        for db_name in db_names:
            marker = (db_name, enc_key)
            if marker in seen:
                continue

            seen.add(marker)
            matched.append({
                "db_name": db_name,
                "enc_key": enc_key,
                "salt": salt,
            })

    matched.sort(key=lambda item: item["db_name"])
    return matched


def format_result(matched: list[dict[str, str]], output_format: str) -> dict[str, str | dict[str, str]]:
    if output_format == "detail":
        return {
            item["db_name"]: {
                "enc_key": item["enc_key"],
                "salt": item["salt"],
            }
            for item in matched
        }

    return {item["db_name"]: item["enc_key"] for item in matched}


def write_output(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n", encoding="utf-8")
    path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    chown_to_sudo_user(path)
    chown_to_sudo_user(path.parent)


def chown_to_sudo_user(path: Path) -> None:
    uid = os.environ.get("SUDO_UID")
    gid = os.environ.get("SUDO_GID")
    if not uid or not gid:
        return

    try:
        os.chown(path, int(uid), int(gid))
    except OSError:
        return


if __name__ == "__main__":
    raise SystemExit(main())
