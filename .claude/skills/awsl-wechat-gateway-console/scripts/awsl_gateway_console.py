#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""CLI helper for the AWSL WeChat Gateway Windows/Linux Console API."""

from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

DEFAULT_TIMEOUT = 400.0
CHAT_LIST_TRAVERSAL_LIMIT = 80
GATEWAY_CLI_PATH = (
    Path(__file__).resolve().parents[2]
    / "awsl-wechat-gateway"
    / "scripts"
    / "awsl_gateway.py"
)


def load_gateway_cli() -> ModuleType:
    spec = importlib.util.spec_from_file_location("awsl_wechat_gateway_cli", GATEWAY_CLI_PATH)
    if spec is None or spec.loader is None:
        raise SystemExit(f"Gateway CLI not found: {GATEWAY_CLI_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gateway = load_gateway_cli()


def request(args: argparse.Namespace, path: str, payload: dict[str, Any]) -> Any:
    return gateway.request_json(
        "POST",
        args.base_url,
        path,
        token=args.token,
        payload=payload,
        timeout=args.timeout,
    )


def state(args: argparse.Namespace) -> dict[str, Any]:
    data = request(args, "/api/console/state", {"max_nodes": args.max_nodes})
    if not isinstance(data, dict):
        raise SystemExit("Console returned an invalid state response")
    return data


def scroll_state(args: argparse.Namespace, direction: str) -> dict[str, Any]:
    data = request(args, "/api/console/scroll-chats", {
        "direction": direction,
        "max_nodes": args.max_nodes,
    })
    if not isinstance(data, dict) or not data.get("available"):
        print_action(data)
    return data


def chat_list_signature(data: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    return tuple(
        (str(item.get("path", "")), str(item.get("name", "")))
        for item in data.get("chat_list", {}).get("items", [])
    )


def resolve_chat_path(args: argparse.Namespace) -> str:
    current = state(args)
    for _ in range(CHAT_LIST_TRAVERSAL_LIMIT):
        previous_signature = chat_list_signature(current)
        current = scroll_state(args, "up")
        if chat_list_signature(current) == previous_signature:
            break
    else:
        raise SystemExit("Could not confirm the top of the main-window chat list")

    for _ in range(CHAT_LIST_TRAVERSAL_LIMIT):
        items = current.get("chat_list", {}).get("items", [])
        matches = [item for item in items if item.get("name") == args.chat]
        if len(matches) > 1:
            raise SystemExit(f"Multiple visible main-window chats have the same name: {args.chat}")
        if matches:
            path = matches[0].get("path")
            if not path:
                raise SystemExit(f"Chat has no accessibility path: {args.chat}")
            return str(path)

        previous_signature = chat_list_signature(current)
        current = scroll_state(args, "down")
        if chat_list_signature(current) == previous_signature:
            raise SystemExit(f"Chat not found in the main-window list: {args.chat}")

    raise SystemExit("Could not confirm the bottom of the main-window chat list")


def print_action(data: Any) -> None:
    gateway.json_dump(data)
    if not isinstance(data, dict) or not data.get("available"):
        raise SystemExit(1)


def command_list(args: argparse.Namespace) -> None:
    print_chat_state(state(args))


def print_chat_state(data: dict[str, Any]) -> None:
    chat_area = data.get("chat_area", {})
    gateway.json_dump({
        "available": data.get("available", False),
        "message": data.get("message", ""),
        "current_chat": chat_area.get("title", "") if chat_area.get("available") else "",
        "chats": data.get("chat_list", {}).get("items", []),
    })


def command_scroll(args: argparse.Namespace) -> None:
    print_chat_state(scroll_state(args, args.direction))


def command_send(args: argparse.Namespace) -> None:
    if args.mention_all and args.message_type != "text":
        raise SystemExit("--mention-all only supports text messages")
    open_result = request(args, "/api/console/open-chat", {
        "path": resolve_chat_path(args),
        "max_nodes": args.max_nodes,
    })
    if not isinstance(open_result, dict) or not open_result.get("available"):
        print_action(open_result)
    chat_area = open_result.get("chat_area", {})
    if not chat_area.get("available") or chat_area.get("title") != args.chat:
        current = chat_area.get("title") or "unrecognized"
        raise SystemExit(f"Failed to open chat {args.chat!r}; current chat is {current!r}")

    message, filename = gateway.load_message_payload(args)
    payload: dict[str, Any] = {
        "chat_name": args.chat,
        "message_type": args.message_type,
        "message": message,
        "mention_all": args.mention_all,
        "max_nodes": args.max_nodes,
    }
    if filename:
        payload["filename"] = filename
    print_action(request(args, "/api/console/send-message", payload))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate the AWSL WeChat Gateway Windows/Linux Console")
    parser.add_argument("-b", "--base-url", default=None, help="Gateway base URL override")
    parser.add_argument("-t", "--token", default=None, help="Gateway Bearer token override")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds")
    parser.add_argument("--no-save", action="store_true", help="Do not update the shared Gateway config cache")
    subparsers = parser.add_subparsers(dest="command", required=True)

    list_parser = subparsers.add_parser("list", help="List main-window chats and current chat")
    list_parser.set_defaults(func=command_list, max_nodes=5000)

    scroll_parser = subparsers.add_parser("scroll", help="Scroll the virtualized main-window chat list")
    scroll_parser.add_argument("direction", choices=("up", "down"))
    scroll_parser.set_defaults(func=command_scroll, max_nodes=5000)

    send_parser = subparsers.add_parser("send", help="Open a chat and send a name-validated message")
    send_parser.add_argument("--chat", required=True, help="Exact chat-list name")
    send_parser.add_argument(
        "--message-type",
        choices=("text", "image", "file", "voice", "voice_file"),
        default="text",
    )
    send_parser.add_argument("--mention-all", action="store_true", help="Native @all for text messages (owner/admin only)")
    send_parser.add_argument("--message", default=None, help="Text content or pre-encoded base64 payload")
    send_parser.add_argument("--file-path", default=None, help="Local attachment to base64 encode")
    send_parser.add_argument("--filename", default=None, help="Filename for file/voice_file messages")
    send_parser.set_defaults(func=command_send, max_nodes=5000)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    cached = gateway.load_cached_config()
    explicit = args.base_url is not None or args.token is not None
    args.base_url = gateway.resolve_base_url(args.base_url, cached)
    args.token = gateway.resolve_token(args.token, cached)
    if not args.no_save:
        gateway.maybe_update_cache(cached, args.base_url, args.token, explicit=explicit)
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
