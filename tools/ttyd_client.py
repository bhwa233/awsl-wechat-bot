#!/usr/bin/env python3
"""
ttyd WebSocket 客户端 — 程序化连接 ttyd 执行命令并捕获输出。

用法:
    # 执行单条命令
    uv run tools/ttyd_client.py --url https://wxbot-ttyd.example.com -c "whoami"

    # 交互模式
    uv run tools/ttyd_client.py --url https://wxbot-ttyd.example.com

    # ws:// 也行
    uv run tools/ttyd_client.py --url ws://192.168.1.x:7681 -c "dir"
"""

import argparse
import asyncio
import json
import re
import sys
import urllib.request

import websockets

# ttyd 1.7.x 协议 (binary frames, ASCII 字符前缀)
# Client → Server: '0'(0x30)=INPUT, '1'(0x31)=RESIZE, '2'=PAUSE, '3'=RESUME
# Server → Client: '0'(0x30)=OUTPUT, '1'(0x31)=TITLE, '2'(0x32)=PREFS
# Auth: 首条消息发 JSON {"AuthToken":"...", "columns":N, "rows":N} (textEncoder.encode)
CLIENT_INPUT = 0x30  # ord('0')
CLIENT_RESIZE = 0x31  # ord('1')
SERVER_OUTPUT = 0x30  # ord('0')


def _derive_urls(url: str) -> tuple[str, str]:
    """从用户给的 URL 推导 WS 和 HTTP(S) base URL。"""
    url = url.rstrip("/")
    if url.startswith("wss://") or url.startswith("ws://"):
        ws_url = url + "/ws" if not url.endswith("/ws") else url
        http_url = ws_url.replace("wss://", "https://").replace("ws://", "http://")
        http_url = http_url.rsplit("/ws", 1)[0]
    else:
        http_url = url
        scheme = "wss" if url.startswith("https") else "ws"
        ws_url = re.sub(r"^https?", scheme, url) + "/ws"
    return ws_url, http_url


def _fetch_token(http_url: str) -> str:
    """从 ttyd /token 端点获取 auth token。"""
    try:
        resp = urllib.request.urlopen(f"{http_url}/token", timeout=10)
        data = json.loads(resp.read())
        return data.get("token", "")
    except Exception:
        return ""


def _make_input(data: bytes) -> bytes:
    """构造 INPUT 消息: 0x30 + data。"""
    payload = bytearray(1 + len(data))
    payload[0] = CLIENT_INPUT
    payload[1:] = data
    return bytes(payload)


async def connect_ttyd(url: str, cols: int = 200, rows: int = 50):
    """建立 ttyd WebSocket 连接, 完成 auth + resize 握手。"""
    ws_url, http_url = _derive_urls(url)
    token = _fetch_token(http_url)

    ws = await websockets.connect(ws_url, subprotocols=["tty"])

    # Auth + resize 合并为首条消息 (和 JS 客户端一致)
    auth_msg = json.dumps({"AuthToken": token, "columns": cols, "rows": rows})
    await ws.send(auth_msg.encode("utf-8"))

    # 等待 shell 初始化 (title/prefs/prompt)
    await _drain(ws, timeout_per_msg=5)

    return ws


async def _drain(ws, timeout_per_msg: float = 3) -> list[str]:
    """读取消息直到超时, 返回 OUTPUT 内容。"""
    parts = []
    for _ in range(200):
        try:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout_per_msg)
            if isinstance(msg, bytes) and len(msg) > 0 and msg[0] == SERVER_OUTPUT:
                parts.append(msg[1:].decode("utf-8", errors="replace"))
        except asyncio.TimeoutError:
            break
    return parts


def _strip_ansi(text: str) -> str:
    """移除 ANSI 转义序列。"""
    return re.sub(r"\x1b\[[0-9;]*[A-Za-z]|\x1b\][^\x07]*\x07|\x1b\[\?[0-9;]*[A-Za-z]", "", text)


async def run_command(
    ws,
    command: str,
    timeout: float = 30.0,
    end_marker: str = "___TTYD_DONE___",
    strip_ansi: bool = True,
) -> str:
    """发送命令并捕获输出。"""
    full_cmd = f"{command} ; echo {end_marker}\r"
    await ws.send(_make_input(full_cmd.encode()))

    output_parts: list[str] = []
    try:
        while True:
            msg = await asyncio.wait_for(ws.recv(), timeout=timeout)
            if not (isinstance(msg, bytes) and len(msg) > 0 and msg[0] == SERVER_OUTPUT):
                continue
            text = msg[1:].decode("utf-8", errors="replace")
            if end_marker in text:
                before = text.split(end_marker)[0]
                if before:
                    output_parts.append(before)
                break
            output_parts.append(text)
    except asyncio.TimeoutError:
        output_parts.append("\n[timeout]\n")

    result = "".join(output_parts)
    if strip_ansi:
        result = _strip_ansi(result)
    return result


async def interactive_mode(ws):
    """交互模式: 实时双向传输。"""

    async def reader():
        try:
            async for msg in ws:
                if isinstance(msg, bytes) and len(msg) > 0 and msg[0] == SERVER_OUTPUT:
                    text = msg[1:].decode("utf-8", errors="replace")
                    sys.stdout.write(text)
                    sys.stdout.flush()
        except websockets.ConnectionClosed:
            pass

    async def writer():
        loop = asyncio.get_event_loop()
        try:
            while True:
                line = await loop.run_in_executor(None, sys.stdin.readline)
                if not line:
                    break
                await ws.send(_make_input(line.encode()))
        except (websockets.ConnectionClosed, EOFError):
            pass

    await asyncio.gather(reader(), writer())


async def main():
    parser = argparse.ArgumentParser(description="ttyd WebSocket 客户端")
    parser.add_argument("--url", required=True, help="ttyd URL (https://... 或 ws://...)")
    parser.add_argument("-c", "--command", default="", help="执行单条命令 (不指定则进入交互模式)")
    parser.add_argument("--timeout", type=float, default=30.0, help="命令超时 (秒)")
    parser.add_argument("--raw", action="store_true", help="不移除 ANSI 转义序列")
    args = parser.parse_args()

    ws = await connect_ttyd(args.url)
    try:
        if args.command:
            output = await run_command(ws, args.command, timeout=args.timeout, strip_ansi=not args.raw)
            print(output)
        else:
            print("[交互模式, Ctrl+D 退出]")
            await interactive_mode(ws)
    finally:
        await ws.close()


if __name__ == "__main__":
    asyncio.run(main())
