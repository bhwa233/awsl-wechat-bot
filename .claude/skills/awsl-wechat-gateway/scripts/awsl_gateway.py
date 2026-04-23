#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""CLI helper for AWSL WeChat Bot Gateway HTTP API."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_TIMEOUT = 15.0


def normalize_base_url(value: str | None) -> str:
    base_url = value or os.getenv("AWSL_GATEWAY_URL") or os.getenv("GATEWAY_URL") or "http://127.0.0.1:8000"
    base_url = base_url.strip()
    if base_url.startswith(("http://", "https://")):
        return base_url.rstrip("/")
    return f"http://{base_url.rstrip('/')}"


def resolve_token(value: str | None) -> str | None:
    token = value or os.getenv("AWSL_GATEWAY_TOKEN") or os.getenv("HTTP_API_TOKEN")
    if token and token.strip():
        return token.strip()
    return None


def json_dump(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def build_url(base_url: str, path: str, params: dict[str, Any] | None = None) -> str:
    url = f"{base_url}{path}"
    if not params:
        return url
    clean_params = {k: v for k, v in params.items() if v is not None}
    query = urllib.parse.urlencode(clean_params)
    if not query:
        return url
    return f"{url}?{query}"


def request_json(
    method: str,
    base_url: str,
    path: str,
    *,
    token: str | None,
    params: dict[str, Any] | None = None,
    payload: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    body = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(build_url(base_url, path, params), data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        handle_http_error(exc)
    except urllib.error.URLError as exc:
        raise SystemExit(f"Gateway request failed: {exc}") from exc

    if not raw:
        return None
    return json.loads(raw)


def handle_http_error(exc: urllib.error.HTTPError) -> None:
    raw = exc.read().decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
        detail = data.get("detail", data)
    except json.JSONDecodeError:
        detail = raw or exc.reason
    raise SystemExit(f"Gateway returned HTTP {exc.code}: {detail}") from exc


def command_health(args: argparse.Namespace) -> None:
    json_dump(request_json("GET", args.base_url, "/api/health", token=args.token, timeout=args.timeout))


def command_windows(args: argparse.Namespace) -> None:
    json_dump(request_json("GET", args.base_url, "/api/groups", token=args.token, timeout=args.timeout))


def command_chatlog_groups(args: argparse.Namespace) -> None:
    data = request_json(
        "GET",
        args.base_url,
        "/api/chatlog/groups",
        token=args.token,
        params={"limit": args.limit},
        timeout=args.timeout,
    )
    json_dump(data)


def command_decrypt(args: argparse.Namespace) -> None:
    data = request_json("POST", args.base_url, "/api/chatlog/decrypt", token=args.token, timeout=args.timeout)
    json_dump(data)


def command_messages(args: argparse.Namespace) -> None:
    data = request_json(
        "GET",
        args.base_url,
        "/api/chatlog/messages",
        token=args.token,
        params={
            "group": args.group,
            "start": args.start,
            "end": args.end,
            "limit": args.limit,
        },
        timeout=args.timeout,
    )
    json_dump(data)


def load_message_payload(args: argparse.Namespace) -> tuple[str, str | None]:
    if args.file_path:
        path = Path(args.file_path).expanduser()
        if not path.is_file():
            raise SystemExit(f"File does not exist: {path}")
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        filename = args.filename or path.name
        return encoded, filename

    if args.message:
        return args.message, args.filename

    raise SystemExit("Provide --message or --file-path")


def command_send(args: argparse.Namespace) -> None:
    message, filename = load_message_payload(args)
    payload: dict[str, Any] = {
        "group_name": args.group,
        "message_type": args.message_type,
        "message": message,
    }
    if filename:
        payload["filename"] = filename
    if args.message_type == "file" and not payload.get("filename"):
        raise SystemExit("--message-type file requires --filename or --file-path")

    json_dump(request_json("POST", args.base_url, "/api/send", token=args.token, payload=payload, timeout=args.timeout))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate AWSL WeChat Bot Gateway HTTP API")
    parser.add_argument("-b", "--base-url", default=None, help="Gateway base URL, e.g. http://127.0.0.1:8000")
    parser.add_argument("-t", "--token", default=None, help="Optional HTTP API Bearer token")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds")

    subparsers = parser.add_subparsers(dest="command", required=True)

    health_parser = subparsers.add_parser("health", help="Check Gateway health")
    health_parser.set_defaults(func=command_health)

    windows_parser = subparsers.add_parser("windows", help="List open/listened WeChat chat windows")
    windows_parser.set_defaults(func=command_windows)

    groups_parser = subparsers.add_parser("chatlog-groups", help="List decrypted chatlog groups")
    groups_parser.add_argument("--limit", type=int, default=0, help="Max groups, 0 means unlimited")
    groups_parser.set_defaults(func=command_chatlog_groups)

    decrypt_parser = subparsers.add_parser("decrypt", help="Decrypt WeChat DB records on the Gateway machine")
    decrypt_parser.set_defaults(func=command_decrypt)

    messages_parser = subparsers.add_parser("messages", help="Query decrypted chat records")
    messages_parser.add_argument("--group", required=True, help="Chatroom ID or personal WeChat ID")
    messages_parser.add_argument("--start", default=None, help="Start time: YYYY-MM-DD or YYYY-MM-DD HH:MM:SS")
    messages_parser.add_argument("--end", default=None, help="End time: YYYY-MM-DD or YYYY-MM-DD HH:MM:SS")
    messages_parser.add_argument("--limit", type=int, default=100, help="Max messages")
    messages_parser.set_defaults(func=command_messages)

    send_parser = subparsers.add_parser("send", help="Send a text/image/file message")
    send_parser.add_argument("--group", required=True, help="Open chat window name")
    send_parser.add_argument("--message-type", choices=("text", "image", "file"), default="text")
    send_parser.add_argument("--message", default=None, help="Text content or pre-encoded base64 payload")
    send_parser.add_argument("--file-path", default=None, help="Local file to base64 encode for image/file sends")
    send_parser.add_argument("--filename", default=None, help="Filename for file messages")
    send_parser.set_defaults(func=command_send)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.base_url = normalize_base_url(args.base_url)
    args.token = resolve_token(args.token)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
