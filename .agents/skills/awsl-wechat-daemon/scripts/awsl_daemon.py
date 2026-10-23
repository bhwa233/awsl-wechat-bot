#!/usr/bin/env -S uv run
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""CLI helper for AWSL WeChat Bot Daemon HTTP API."""

from __future__ import annotations

import argparse
import http.client
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_TIMEOUT = 15.0
UPDATE_TIMEOUT = 120.0
CONFIG_PATH = Path.home() / ".config" / "awsl-wechat-daemon" / "config.json"


def load_cached_config() -> dict[str, Any]:
    if not CONFIG_PATH.is_file():
        return {}
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_cached_config(data: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = CONFIG_PATH.with_suffix(CONFIG_PATH.suffix + ".tmp")
    tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    try:
        os.chmod(tmp_path, 0o600)
    except OSError:
        pass
    tmp_path.replace(CONFIG_PATH)


def _format_base_url(value: str) -> str:
    value = value.strip()
    if value.startswith(("http://", "https://")):
        return value.rstrip("/")
    return f"http://{value.rstrip('/')}"


def resolve_base_url(value: str | None, cached: dict[str, Any]) -> str:
    raw = value or cached.get("base_url")
    if not raw:
        raise SystemExit(
            "Daemon base URL not configured. Run `config set --base-url <url>` "
            "or pass --base-url."
        )
    return _format_base_url(str(raw))


def resolve_token(value: str | None, cached: dict[str, Any]) -> str | None:
    token = value if value is not None else cached.get("token")
    if token and str(token).strip():
        return str(token).strip()
    return None


def maybe_update_cache(cached: dict[str, Any], base_url: str, token: str | None, *, explicit: bool) -> None:
    if not explicit:
        return
    new_data = dict(cached)
    new_data["base_url"] = base_url
    if token:
        new_data["token"] = token
    if new_data == cached:
        return
    try:
        save_cached_config(new_data)
    except OSError as exc:
        print(f"warning: failed to update daemon config cache: {exc}", file=sys.stderr)


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
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(build_url(base_url, path, params), headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        handle_http_error(exc)
    except urllib.error.URLError as exc:
        raise SystemExit(f"Daemon request failed: {exc}") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise SystemExit(f"Daemon request failed: {exc}") from exc

    if not raw:
        return None
    return json.loads(raw)


def request_text(
    method: str,
    base_url: str,
    path: str,
    *,
    token: str | None,
    params: dict[str, Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> str:
    headers = {"Accept": "text/plain"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(build_url(base_url, path, params), headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        handle_http_error(exc)
    except urllib.error.URLError as exc:
        raise SystemExit(f"Daemon request failed: {exc}") from exc
    except (OSError, http.client.HTTPException) as exc:
        raise SystemExit(f"Daemon request failed: {exc}") from exc
    return ""


def handle_http_error(exc: urllib.error.HTTPError) -> None:
    raw = exc.read().decode("utf-8", errors="replace")
    try:
        data = json.loads(raw)
        detail = data.get("detail", data)
    except json.JSONDecodeError:
        detail = raw or exc.reason
    raise SystemExit(f"Daemon returned HTTP {exc.code}: {detail}") from exc


def command_health(args: argparse.Namespace) -> None:
    json_dump(request_json("GET", args.base_url, "/health", token=args.token, timeout=args.timeout))


def command_status(args: argparse.Namespace) -> None:
    json_dump(request_json("GET", args.base_url, "/status", token=args.token, timeout=args.timeout))


def command_start(args: argparse.Namespace) -> None:
    json_dump(request_json("POST", args.base_url, "/start", token=args.token, timeout=args.timeout))


def command_stop(args: argparse.Namespace) -> None:
    json_dump(request_json("POST", args.base_url, "/stop", token=args.token, timeout=args.timeout))


def command_restart(args: argparse.Namespace) -> None:
    json_dump(request_json("POST", args.base_url, "/restart", token=args.token, timeout=args.timeout))


def command_update(args: argparse.Namespace) -> None:
    json_dump(request_json("POST", args.base_url, "/update", token=args.token, timeout=UPDATE_TIMEOUT))


def command_logs(args: argparse.Namespace) -> None:
    text = request_text("GET", args.base_url, "/logs", token=args.token, params={"lines": args.lines}, timeout=args.timeout)
    print(text)


def command_config(args: argparse.Namespace) -> None:
    cached = load_cached_config()
    if args.action == "show":
        json_dump({"path": str(CONFIG_PATH), **cached})
        return
    if args.action == "clear":
        if CONFIG_PATH.is_file():
            CONFIG_PATH.unlink()
        json_dump({"cleared": True, "path": str(CONFIG_PATH)})
        return
    if args.action == "set":
        new_data = dict(cached)
        if args.base_url_value is not None:
            new_data["base_url"] = _format_base_url(args.base_url_value)
        if args.token_value is not None:
            token_clean = args.token_value.strip()
            if token_clean:
                new_data["token"] = token_clean
            else:
                new_data.pop("token", None)
        save_cached_config(new_data)
        json_dump({"saved": True, "path": str(CONFIG_PATH), **new_data})
        return
    raise SystemExit(f"unknown config action: {args.action}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Operate AWSL WeChat Bot Daemon HTTP API")
    parser.add_argument("-b", "--base-url", default=None, help="Daemon base URL, e.g. http://127.0.0.1:8001")
    parser.add_argument("-t", "--token", default=None, help="Optional HTTP API Bearer token")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="HTTP timeout in seconds")
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Do not cache --base-url/--token to ~/.config/awsl-wechat-daemon/config.json",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    config_parser = subparsers.add_parser("config", help="Manage cached connection info")
    config_sub = config_parser.add_subparsers(dest="action", required=True)
    config_sub.add_parser("show", help="Print cached config")
    config_sub.add_parser("clear", help="Remove the cached config file")
    config_set = config_sub.add_parser("set", help="Persist base URL and/or token")
    config_set.add_argument("--base-url", dest="base_url_value", default=None, help="Daemon base URL to cache")
    config_set.add_argument("--token", dest="token_value", default=None, help="Bearer token to cache (empty to remove)")
    config_parser.set_defaults(func=command_config)

    health_parser = subparsers.add_parser("health", help="Check Daemon health")
    health_parser.set_defaults(func=command_health)

    status_parser = subparsers.add_parser("status", help="Get bot process status")
    status_parser.set_defaults(func=command_status)

    start_parser = subparsers.add_parser("start", help="Start the bot process")
    start_parser.set_defaults(func=command_start)

    stop_parser = subparsers.add_parser("stop", help="Stop the bot process")
    stop_parser.set_defaults(func=command_stop)

    restart_parser = subparsers.add_parser("restart", help="Restart the bot process")
    restart_parser.set_defaults(func=command_restart)

    update_parser = subparsers.add_parser("update", help="Stop bot, git pull, uv sync, restart")
    update_parser.set_defaults(func=command_update)

    logs_parser = subparsers.add_parser("logs", help="Tail bot process logs")
    logs_parser.add_argument("--lines", "-n", type=int, default=100, help="Number of log lines (1-10000)")
    logs_parser.set_defaults(func=command_logs)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "config":
        args.func(args)
        return 0
    cached = load_cached_config()
    explicit_base = args.base_url is not None
    explicit_token = args.token is not None
    args.base_url = resolve_base_url(args.base_url, cached)
    args.token = resolve_token(args.token, cached)
    if not args.no_save:
        maybe_update_cache(
            cached,
            args.base_url,
            args.token,
            explicit=explicit_base or explicit_token,
        )
    args.func(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
