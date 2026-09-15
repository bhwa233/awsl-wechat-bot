#!/usr/bin/env python3
"""
Linux-only WeChat SQLCipher key extractor.

微信 4.1.13 起, 进程内存不再缓存明文 per-db 密钥 (x'...' 字面量扫描返回 0),
只保留一个 32 字节 passphrase, 在登录时经 WCDB cipher 配置函数传入:

    enc_key = PBKDF2-HMAC-SHA512(passphrase, db_salt, 256000, dklen=32)

而该配置函数会被调用多次, 首个 32 字节参数不一定是 passphrase。因此捕获时
必须对每个候选当场用数据库 page-1 的 HMAC 验证, 不通过就重新布断点等下一次
调用 (验证式捕获)。

两种模式:
  - scan        (旧, 微信 <= 4.1.12): 微信运行中, 扫描内存 x'...' 字面量
  - passphrase  (新, 微信 >= 4.1.1x): 关闭微信后由本工具重启微信,
                ptrace 断点捕获 passphrase 并逐库 PBKDF2 派生验证
  - auto        (默认): 先 scan, 失败则提示走 passphrase 模式

用法:
    sudo uv run tools/extract_wx_keys_linux.py --json            # auto
    uv run tools/extract_wx_keys_linux.py --mode passphrase      # 4.1.13+
    uv run tools/extract_wx_keys_linux.py --passphrase <64hex>   # 复用已知 passphrase 只派生
    sudo uv run tools/extract_wx_keys_linux.py --db-dir /path/to/db_storage --json

passphrase 模式纯标准库实现 (ctypes ptrace), 无 gdb 依赖; 以微信所属用户运行
即可 (追踪自己启动的子进程不受 yama ptrace_scope 限制)。

致谢: 验证式捕获与断点定位算法参考
  - boujuan/extract-wechat-messages-linux (MIT, 已在 4.1.13.9 验证)
  - TANGandXUE/wcdb-key-tool (MIT, passphrase -> PBKDF2 派生思路)

The default JSON format can be used directly as WECHAT_DB_KEY on Linux:

    {"contact/contact.db": "64hex", "message/message_0.db": "64hex"}
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import hmac
import json
import os
import platform
import pwd
import re
import select
import signal
import stat
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import BinaryIO

PATTERN_RE = re.compile(rb"x'([0-9a-fA-F]{96})'")
HEX_PATTERN_LEN = 96
PATTERN_TOTAL_LEN = HEX_PATTERN_LEN + 3
CHUNK_SIZE = 2 * 1024 * 1024
OVERLAP_SIZE = PATTERN_TOTAL_LEN
SQLITE_HEADER_PREFIX = b"SQLite format 3"

# SQLCipher 4 常量 (与 chatlog/wechat_db.py 保持一致)
PAGE_SIZE = 4096
SALT_SIZE = 16
KEY_SIZE = 32
HMAC_SHA512_SIZE = 64
RESERVE_SIZE = 80  # IV(16) + HMAC(64)
DATA_END = PAGE_SIZE - RESERVE_SIZE + 16
KDF_ITER = 256000


# ============================================================
# 通用: 数据库发现 / 校验
# ============================================================

def verify_enc_key(enc_key: bytes, page1: bytes) -> bool:
    """SQLCipher 4 page-1 HMAC 校验 (direct-key 语义, 与 WECHAT_DB_KEY 一致)."""
    if len(page1) < PAGE_SIZE or len(enc_key) != KEY_SIZE:
        return False
    salt = page1[:SALT_SIZE]
    mac_salt = bytes(b ^ 0x3A for b in salt)
    mac_key = hashlib.pbkdf2_hmac("sha512", enc_key, mac_salt, 2, KEY_SIZE)
    h = hmac.new(mac_key, digestmod=hashlib.sha512)
    h.update(page1[SALT_SIZE:DATA_END])
    h.update(struct.pack("<I", 1))
    return h.digest() == page1[DATA_END:DATA_END + HMAC_SHA512_SIZE]


def derive_key(passphrase: bytes, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha512", passphrase, salt, KDF_ITER, KEY_SIZE)


def collect_db_pages(db_dir: Path) -> list[dict]:
    """[{rel, salt_hex, page1}] 跳过明文 sqlite 与过小文件."""
    out: list[dict] = []
    for path in sorted(db_dir.rglob("*.db")):
        try:
            if path.stat().st_size < PAGE_SIZE:
                continue
            with path.open("rb") as f:
                page1 = f.read(PAGE_SIZE)
        except OSError:
            continue
        if len(page1) < PAGE_SIZE or page1[:15] == SQLITE_HEADER_PREFIX:
            continue
        out.append({
            "rel": path.relative_to(db_dir).as_posix(),
            "salt_hex": page1[:SALT_SIZE].hex(),
            "page1": page1,
        })
    return out


# ============================================================
# ELF 静态分析: 定位 WCDB cipher 配置函数
# ============================================================

_WCDB_ANCHOR = b"com.Tencent.WCDB.Config.Cipher"
_LEA_RSI = b"\x48\x8D\x35"   # lea rsi, [rip+disp32]
_LEA_RDI = b"\x48\x8D\x3D"   # lea rdi, [rip+disp32]
_FUNC_HEAD = b"\x55\x41\x57"  # push rbp; push r15


class HookNotFoundError(RuntimeError):
    pass


def _elf_sections(path: Path) -> dict[str, tuple[int, bytes]]:
    data = path.read_bytes()
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        raise HookNotFoundError(f"{path}: 不是小端 ELF64 二进制")
    e_shoff, = struct.unpack_from("<Q", data, 0x28)
    e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", data, 0x3A)
    headers = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        headers.append(struct.unpack_from("<IIQQQQ", data, off))  # name,type,flags,addr,offset,size
    shstr_off = headers[e_shstrndx][4]
    out: dict[str, tuple[int, bytes]] = {}
    for name, _stype, _flags, addr, offset, size in headers:
        end = data.index(b"\0", shstr_off + name)
        sname = data[shstr_off + name:end].decode("ascii", "replace")
        if sname in (".rodata", ".text"):
            out[sname] = (addr, data[offset:offset + size])
    if ".rodata" not in out or ".text" not in out:
        raise HookNotFoundError(f"{path}: 缺少 .rodata/.text 节")
    return out


def _rip_relative_refs(text: bytes, text_va: int, opcode: bytes, target_va: int) -> list[int]:
    hits: list[int] = []
    for off in range(len(text) - 6):
        if text[off:off + 3] != opcode:
            continue
        disp, = struct.unpack_from("<i", text, off + 3)
        if text_va + off + 7 + disp == target_va:
            hits.append(off)
    return hits


def find_hook_offset(binary: Path) -> int:
    """锚点串 --(lea rsi)--> 同时含 lea rdi 的调用方 --(对该结构 lea rsi)-->
    消费函数; 向回找 <= 0x500 字节的函数序言. 微信更新后仍可自适应。"""
    sections = _elf_sections(binary)
    rodata_va, rodata = sections[".rodata"]
    text_va, text = sections[".text"]
    candidates: list[int] = []
    pos = 0
    while True:
        ao = rodata.find(_WCDB_ANCHOR, pos)
        if ao == -1:
            break
        pos = ao + 1
        anchor_va = rodata_va + ao
        for f1 in _rip_relative_refs(text, text_va, _LEA_RSI, anchor_va):
            if f1 < 7 or text[f1 - 7:f1 - 4] != _LEA_RDI:
                continue
            unk_disp, = struct.unpack_from("<i", text, f1 - 4)
            unk_va = text_va + f1 + unk_disp
            for f2 in _rip_relative_refs(text, text_va, _LEA_RSI, unk_va):
                for c in range(f2, max(0, f2 - 0x500) - 1, -1):
                    if text[c:c + 3] == _FUNC_HEAD:
                        va = text_va + c
                        if va not in candidates:
                            candidates.append(va)
                        break
    if not candidates:
        raise HookNotFoundError(
            f"{binary}: 未找到 WCDB cipher 配置函数 (锚点 {_WCDB_ANCHOR!r} xref 链失败)")
    return sorted(candidates)[0]


# ============================================================
# ptrace 捕获 tracer
# ============================================================

PTRACE_PEEKDATA = 2
PTRACE_POKEDATA = 5
PTRACE_CONT = 7
PTRACE_SINGLESTEP = 9
PTRACE_GETREGS = 12
PTRACE_SETREGS = 13
PTRACE_ATTACH = 16
PTRACE_DETACH = 17
PTRACE_SETOPTIONS = 0x4200
PTRACE_O_TRACEFORK = 1
PTRACE_O_TRACEVFORK = 2
PTRACE_O_TRACECLONE = 8
PTRACE_O_TRACEEXEC = 16
_PTRACE_OPTS = (
    PTRACE_O_TRACEFORK | PTRACE_O_TRACEVFORK | PTRACE_O_TRACECLONE | PTRACE_O_TRACEEXEC
)
PTRACE_EVENT_FORK = 1
PTRACE_EVENT_VFORK = 2
PTRACE_EVENT_CLONE = 3
PTRACE_EVENT_EXEC = 4
INT3 = 0xCC
_PTR_MIN, _PTR_MAX = 0x1000, 0x7FFFFFFFFFFF
_WALL = 0x40000000


class _user_regs_struct(ctypes.Structure):
    _fields_ = [(n, ctypes.c_ulonglong) for n in (
        "r15", "r14", "r13", "r12", "rbp", "rbx", "r11", "r10", "r9", "r8",
        "rax", "rcx", "rdx", "rsi", "rdi", "orig_rax", "rip", "cs", "eflags",
        "rsp", "ss", "fs_base", "gs_base", "ds", "es", "fs", "gs",
    )]


_libc = ctypes.CDLL("libc.so.6", use_errno=True)
_libc.ptrace.restype = ctypes.c_long
_libc.ptrace.argtypes = [ctypes.c_long, ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p]


class CaptureError(RuntimeError):
    pass


def _ptrace(req: int, pid: int, addr: int = 0, data: int = 0) -> int:
    ctypes.set_errno(0)
    val = _libc.ptrace(req, pid, ctypes.c_void_p(addr), ctypes.c_void_p(data))
    if val == -1 and ctypes.get_errno() != 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e))
    return val


def _peek(pid: int, addr: int) -> int:
    ctypes.set_errno(0)
    val = _libc.ptrace(PTRACE_PEEKDATA, pid, ctypes.c_void_p(addr), ctypes.c_void_p(0))
    if val == -1 and ctypes.get_errno() != 0:
        raise OSError(ctypes.get_errno(), "PEEKDATA")
    return val & 0xFFFFFFFFFFFFFFFF


def _read_mem(pid: int, addr: int, n: int) -> bytes:
    out = bytearray()
    for off in range(0, n, 8):
        out += struct.pack("<Q", _peek(pid, addr + off))
    return bytes(out[:n])


def _getregs(pid: int) -> _user_regs_struct:
    regs = _user_regs_struct()
    _ptrace(PTRACE_GETREGS, pid, 0, ctypes.addressof(regs))
    return regs


def _setregs(pid: int, regs: _user_regs_struct) -> None:
    _ptrace(PTRACE_SETREGS, pid, 0, ctypes.addressof(regs))


def _pids_of_exe(binary: str) -> set[int]:
    out: set[int] = set()
    for entry in os.scandir("/proc"):
        if not entry.name.isdigit():
            continue
        try:
            if os.readlink(f"/proc/{entry.name}/exe") == binary:
                out.add(int(entry.name))
        except OSError:
            continue
    return out


def _image_base(pid: int, binary: str, wait: float = 3.0) -> int:
    """PIE 加载基址: 文件偏移 0 的映射起始 (不能用 r-xp 段, 见 wcdb-key-tool#2)."""
    deadline = time.monotonic() + wait
    while True:
        try:
            with open(f"/proc/{pid}/maps") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 6 and parts[5] == binary and parts[2] == "00000000":
                        return int(parts[0].split("-")[0], 16)
        except OSError:
            pass
        if time.monotonic() > deadline:
            raise CaptureError(f"pid {pid} 找不到 {binary} 的映射基址")
        time.sleep(0.01)


def _passphrase_candidates(pid: int, regs: _user_regs_struct) -> list[bytes]:
    """两种已观察到的调用布局 (rsi 为第 2 参):
    1. rsi 直接指向 32 字节, rdx == 32
    2. rsi 指向结构体: +8 为数据指针, +16 为 size == 32"""
    cands: list[bytes] = []
    rsi, rdx = regs.rsi, regs.rdx
    if _PTR_MIN < rsi < _PTR_MAX:
        if rdx == 32:
            try:
                cands.append(_read_mem(pid, rsi, 32))
            except OSError:
                pass
        try:
            hdr = _read_mem(pid, rsi, 24)
            ptr, size = struct.unpack_from("<QQ", hdr, 8)
            if size == 32 and _PTR_MIN < ptr < _PTR_MAX:
                cands.append(_read_mem(pid, ptr, 32))
        except OSError:
            pass
    return [c for c in cands if len(c) == 32]


def _tracer(binary: str, hook_off: int, timeout: float,
            probe_page1: bytes, launch_cmd: list[str] | None) -> bytes:
    import subprocess as sp

    pre_existing = _pids_of_exe(binary)
    if pre_existing:
        raise CaptureError("微信正在运行, 请先关闭 (passphrase 只在登录时计算)")

    argv = launch_cmd if launch_cmd else [binary]
    proc = sp.Popen(argv, stdout=sp.DEVNULL, stderr=sp.DEVNULL, start_new_session=True)

    deadline = time.monotonic() + min(timeout, 30.0)
    target = None
    while time.monotonic() < deadline:
        new = _pids_of_exe(binary) - pre_existing
        if new:
            target = min(new)
            break
        time.sleep(0.002)
    if target is None:
        raise CaptureError("微信进程未启动 (launcher 模式下请用 --wechat-bin 指向真实二进制)")

    try:
        _ptrace(PTRACE_ATTACH, target)
    except OSError as e:
        raise CaptureError(f"PTRACE_ATTACH 失败 (pid {target}): {e}; "
                           "尝试 sudo sysctl kernel.yama.ptrace_scope=0") from e
    os.waitpid(target, 0)
    tracked: set[int] = {target}
    try:
        tids = [int(t) for t in os.listdir(f"/proc/{target}/task")]
    except OSError:
        tids = []
    for tid in tids:
        if tid in tracked:
            continue
        try:
            _ptrace(PTRACE_ATTACH, tid)
            os.waitpid(tid, 0)
            tracked.add(tid)
        except OSError:
            pass
    for tid in tracked:
        _ptrace(PTRACE_SETOPTIONS, tid, 0, _PTRACE_OPTS)

    base = _image_base(target, binary)
    bp_addr = base + hook_off
    word_addr = bp_addr & ~7
    word_shift = (bp_addr & 7) * 8
    orig_word = _peek(target, word_addr)
    _ptrace(PTRACE_POKEDATA, target, word_addr,
            (orig_word & ~(0xFF << word_shift)) | (INT3 << word_shift))
    armed = True
    print(f"已附加 pid {target}, 断点 {bp_addr:#x} (base={base:#x}+{hook_off:#x})", file=sys.stderr)
    for tid in tracked:
        _ptrace(PTRACE_CONT, tid, 0, 0)
    print(f"等待微信登录触发密钥初始化 (最长 {timeout:.0f}s)...", file=sys.stderr)

    def teardown() -> None:
        """detach 不会唤醒 group-stop 的线程, 必须逐个 CONT 再 DETACH, 最后 SIGCONT,
        否则微信 UI 会冻死。"""
        drain_deadline = time.monotonic() + 1.0
        while time.monotonic() < drain_deadline:
            try:
                pid, _st = os.waitpid(-1, os.WNOHANG | _WALL)
            except ChildProcessError:
                break
            if pid == 0:
                break
            tracked.add(pid)
            try:
                _ptrace(PTRACE_CONT, pid, 0, 0)
            except OSError:
                pass
        for tid in list(tracked):
            try:
                _ptrace(PTRACE_DETACH, tid)
            except OSError:
                pass
        try:
            os.kill(target, signal.SIGCONT)
        except OSError:
            pass

    deadline = time.monotonic() + timeout
    try:
        while True:
            try:
                pid, status = os.waitpid(-1, os.WNOHANG | _WALL)
            except ChildProcessError as e:
                raise CaptureError("微信在捕获前退出") from e
            if pid == 0:
                if time.monotonic() > deadline:
                    raise CaptureError(
                        f"等待 {timeout:.0f}s 超时; 请确保微信完成了登录 (扫码/自动重登)")
                time.sleep(0.02)
                continue
            if os.WIFEXITED(status) or os.WIFSIGNALED(status):
                tracked.discard(pid)
                if pid == target:
                    code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -os.WTERMSIG(status)
                    raise CaptureError(f"微信在捕获期间退出 (status {code})")
                continue
            if not os.WIFSTOPPED(status):
                continue
            sig = os.WSTOPSIG(status)
            event = (status >> 16) & 0xFF
            tracked.add(pid)
            if event in (PTRACE_EVENT_FORK, PTRACE_EVENT_VFORK, PTRACE_EVENT_CLONE):
                try:
                    _ptrace(PTRACE_SETOPTIONS, pid, 0, _PTRACE_OPTS)
                except OSError:
                    pass
                _ptrace(PTRACE_CONT, pid, 0, 0)
                continue
            if sig == signal.SIGTRAP:
                regs = _getregs(pid)
                if armed and regs.rip - 1 == bp_addr:
                    captured = None
                    for cand in _passphrase_candidates(pid, regs):
                        if verify_enc_key(derive_key(cand, probe_page1[:SALT_SIZE]), probe_page1):
                            captured = cand
                            break
                    _ptrace(PTRACE_POKEDATA, pid, word_addr, orig_word)
                    regs.rip = bp_addr
                    _setregs(pid, regs)
                    _ptrace(PTRACE_SINGLESTEP, pid, 0, 0)
                    try:
                        os.waitpid(pid, 0)
                    except ChildProcessError:
                        pass
                    if captured is not None:
                        _ptrace(PTRACE_CONT, pid, 0, 0)
                        return captured
                    # 该次调用的 32 字节不是 passphrase, 重新布断点等下一次
                    _ptrace(PTRACE_POKEDATA, pid, word_addr,
                            (orig_word & ~(0xFF << word_shift)) | (INT3 << word_shift))
                    _ptrace(PTRACE_CONT, pid, 0, 0)
                    continue
                _ptrace(PTRACE_CONT, pid, 0, 0)
                continue
            if sig in (signal.SIGSTOP, signal.SIGTSTP, signal.SIGTTIN, signal.SIGTTOU):
                _ptrace(PTRACE_CONT, pid, 0, 0)
                continue
            _ptrace(PTRACE_CONT, pid, 0, sig)
    finally:
        teardown()


def capture_passphrase(binary: Path, probe_page1: bytes, timeout: float = 300.0,
                       launch_cmd: list[str] | None = None) -> bytes:
    """在独立 fork 的 helper 里跑 tracer, 避免 waitpid(-1) 抢走无关子进程状态。"""
    hook_off = find_hook_offset(binary)
    print(f"WCDB cipher 配置函数偏移: {hook_off:#x}", file=sys.stderr)
    binary_str = str(binary)

    err_r, res_w = os.pipe()
    helper = os.fork()
    if helper == 0:
        os.close(err_r)
        code = 0
        try:
            ph = _tracer(binary_str, hook_off, timeout, probe_page1, launch_cmd)
            os.write(res_w, b"OK" + ph.hex().encode())
        except BaseException as e:  # helper 不能把异常抛回父进程
            try:
                os.write(res_w, b"ERR" + repr(e).encode())
            except OSError:
                pass
            code = 1
        finally:
            os.close(res_w)
            os._exit(code)

    os.close(res_w)
    payload = b""
    deadline = time.monotonic() + timeout + 30
    try:
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                try:
                    os.kill(helper, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                raise CaptureError("捕获 helper 未按时结束")
            r, _w, _e = select.select([err_r], [], [], min(remain, 0.5))
            if not r:
                continue
            chunk = os.read(err_r, 65536)
            if not chunk:
                break
            payload += chunk
    finally:
        os.close(err_r)
        try:
            os.waitpid(helper, 0)
        except ChildProcessError:
            pass
    if payload.startswith(b"OK") and len(payload) == 2 + 64:
        ph = bytes.fromhex(payload[2:].decode())
        for pid in _pids_of_exe(binary_str):
            try:
                os.kill(pid, signal.SIGCONT)
            except OSError:
                pass
        return ph
    msg = payload[3:].decode("utf-8", "replace") if payload.startswith(b"ERR") \
        else payload.decode("utf-8", "replace")
    raise CaptureError(msg or "捕获 helper 无输出")


# ============================================================
# 旧路径: 内存字面量扫描 (微信 <= 4.1.12)
# ============================================================

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
        help="WeChat process PID (scan 模式自动探测).",
    )
    parser.add_argument(
        "--process",
        nargs="+",
        default=["wechat", "weixin"],
        help="Process names to match in /proc/<pid>/comm.",
    )
    parser.add_argument(
        "--mode",
        choices=["auto", "scan", "passphrase"],
        default="auto",
        help="auto: 先内存扫描, 失败提示 passphrase 模式; scan: 仅旧版内存扫描; "
             "passphrase: 重启微信捕获 (4.1.13+)",
    )
    parser.add_argument(
        "--passphrase",
        help="直接用已知 passphrase (64 hex) 派生, 跳过捕获",
    )
    parser.add_argument(
        "--wechat-bin",
        type=Path,
        default=Path("/opt/wechat/wechat"),
        help="微信主二进制路径 (passphrase 模式)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=300.0,
        help="passphrase 捕获等待登录的超时秒数 (默认 300)",
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
            # 微信 4.1.13+ 的默认数据根
            home / ".xwechat" / "xwechat_files",
            home / "xwechat_files",
            home / "Documents" / "xwechat_files",
            home / "文档" / "xwechat_files",
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


# ============================================================
# main
# ============================================================

def _emit(matched: list[dict[str, str]], args: argparse.Namespace) -> int:
    result = format_result(matched, args.output_format)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        write_output(args.output, text)
        print(f"密钥已保存: {args.output}", file=sys.stderr)
        return 0
    print(text)
    return 0


def _derive_and_match(passphrase: bytes, db_pages: list[dict]) -> list[dict[str, str]]:
    matched: list[dict[str, str]] = []
    for db in db_pages:
        key = derive_key(passphrase, bytes.fromhex(db["salt_hex"]))
        if verify_enc_key(key, db["page1"]):
            matched.append({
                "db_name": db["rel"],
                "enc_key": key.hex(),
                "salt": db["salt_hex"],
            })
    matched.sort(key=lambda item: item["db_name"])
    return matched


def _launch_env() -> dict[str, str]:
    """保证重启微信时能连上 X 会话."""
    env = dict(os.environ)
    env.setdefault("DISPLAY", ":0")
    home = env.get("HOME", str(Path.home()))
    env.setdefault("XAUTHORITY", str(Path(home) / ".Xauthority"))
    return env


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

    print(f"数据目录: {db_dir}", file=sys.stderr)
    db_pages = collect_db_pages(db_dir)
    print(f"找到 {len(db_pages)} 个加密数据库", file=sys.stderr)
    if not db_pages:
        print("错误: 没有找到加密 .db 文件", file=sys.stderr)
        return 1

    # 0) 已知 passphrase, 只派生
    if args.passphrase:
        try:
            passphrase = bytes.fromhex(args.passphrase.strip())
        except ValueError:
            print("错误: --passphrase 必须是 64 位十六进制", file=sys.stderr)
            return 1
        if len(passphrase) != KEY_SIZE:
            print(f"错误: --passphrase 长度必须是 {KEY_SIZE} 字节", file=sys.stderr)
            return 1
        matched = _derive_and_match(passphrase, db_pages)
        print(f"passphrase 派生验证: {len(matched)}/{len(db_pages)}", file=sys.stderr)
        if not matched:
            print("错误: passphrase 对当前数据库无效 (可能已轮换)", file=sys.stderr)
            return 2
        return _emit(matched, args)

    pid = args.pid or find_wechat_pid(args.process)

    # 1) 旧版内存扫描
    if args.mode in ("auto", "scan"):
        if not pid:
            if args.mode == "scan":
                print("错误: scan 模式需要微信正在运行", file=sys.stderr)
                return 1
        else:
            print(f"微信 PID: {pid}, 执行内存字面量扫描...", file=sys.stderr)
            try:
                raw_keys = scan_process_keys(pid)
            except PermissionError:
                print(f"错误: 无法读取 /proc/{pid}/mem, 请使用 sudo 运行", file=sys.stderr)
                return 1
            except OSError as exc:
                print(f"错误: 扫描进程内存失败: {exc}", file=sys.stderr)
                return 1

            print(f"找到 {len(raw_keys)} 个候选密钥", file=sys.stderr)
            matched = match_keys(collect_db_salts(db_dir), raw_keys)
            print(f"匹配到 {len(matched)} 个数据库密钥", file=sys.stderr)
            if matched or args.mode == "scan":
                return _emit(matched, args)
            print("内存未发现明文密钥, 微信可能 >= 4.1.13, 尝试 passphrase 捕获...", file=sys.stderr)

    # 2) passphrase 捕获
    if pid:
        print(
            "错误: passphrase 捕获需要先关闭微信 (工具会自行重启微信并等待登录)。\n"
            "     关闭微信后重新运行本工具, 或加 --mode passphrase",
            file=sys.stderr,
        )
        return 3

    if not args.wechat_bin.is_file():
        print(f"错误: 微信二进制不存在: {args.wechat_bin} (用 --wechat-bin 指定)", file=sys.stderr)
        return 1

    probe = db_pages[0]["page1"]
    try:
        passphrase = capture_passphrase(
            args.wechat_bin, probe, timeout=args.timeout,
        )
    except (CaptureError, HookNotFoundError) as exc:
        print(f"错误: passphrase 捕获失败: {exc}", file=sys.stderr)
        return 1

    print(f"passphrase 捕获成功: {passphrase.hex()[:8]}... "
          f"(完整值如下, 可用 --passphrase 复用免再次捕获)", file=sys.stderr)
    print(f"PASSPHRASE={passphrase.hex()}", file=sys.stderr)

    matched = _derive_and_match(passphrase, db_pages)
    print(f"PBKDF2 派生验证: {len(matched)}/{len(db_pages)}", file=sys.stderr)
    if not matched:
        print("错误: 派生后未能验证任何密钥", file=sys.stderr)
        return 2
    return _emit(matched, args)


if __name__ == "__main__":
    raise SystemExit(main())
