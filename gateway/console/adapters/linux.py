from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path

from gateway.adapters.linux import LinuxWeChatAdapter
from gateway.console.adapters.base import BaseConsoleAdapter
from gateway.utils.linux_accessibility import chat_title, input_node
from gateway.utils.voice_synthesis import synthesize_voice

MAIN_TITLES = {"微信", "WeChat", "Weixin"}
CHAT_LIST_NAMES = {"会话", "聊天", "Chats"}
MESSAGE_LIST_NAMES = {"消息", "Messages"}


def operation(method):
    @wraps(method)
    def wrapped(self, *args, **kwargs):
        if not self._console_lock.acquire(blocking=False):
            return self._error("Console 正在执行其他操作")
        try:
            with self.adapter.ui_operation_lock:
                return method(self, *args, **kwargs)
        except Exception as exc:
            return self._error(str(exc))
        finally:
            self._console_lock.release()
    return wrapped


class LinuxConsoleAdapter(BaseConsoleAdapter):
    def __init__(self, adapter: LinuxWeChatAdapter):
        self.adapter = adapter
        self._console_lock = threading.Lock()
        self._paths: OrderedDict[str, dict] = OrderedDict()
        self._paths_lock = threading.Lock()

    @staticmethod
    def _error(message):
        return {"available": False, "platform": "linux", "message": message}

    @staticmethod
    def _result(**values):
        return {"available": True, "platform": "linux", "message": "", **values}

    def _windows(self, *, main=False):
        self.adapter._refresh_group_cache()
        return self.adapter.list_windows(include_main=main)

    def _main(self):
        windows = [w for w in self._windows(main=True) if w.title in MAIN_TITLES]
        if len(windows) != 1:
            raise RuntimeError("无法唯一定位微信主窗口")
        return windows[0]

    def _window(self, handle):
        if handle is None:
            return self._main()
        windows = [w for w in self._windows(main=True) if int(w.window_id, 16) == handle]
        if len(windows) != 1:
            raise RuntimeError("未找到指定微信窗口")
        return windows[0]

    @staticmethod
    def _window_info(window):
        return {
            "name": window.title, "native_window_handle": int(window.window_id, 16),
            "class_name": "wechat.wechat", "framework_id": "AT-SPI",
            "is_main": window.title in MAIN_TITLES,
        }

    @staticmethod
    def _list_node(nodes, names):
        return next((n for n in nodes if n["role"] == "list" and n["name"] in names), None)

    @staticmethod
    def _children(nodes, parent):
        if parent is None:
            return []
        return [n for n in nodes if n["depth"] == parent["depth"] + 1 and n["path"].startswith(parent["path"] + ".")]

    @staticmethod
    def _session_name(raw):
        return re.sub(r"\s*\[\d+(?:条)?\]\s*$", "", raw.splitlines()[0]).strip()

    @staticmethod
    def _looks_like_time(text):
        return bool(re.fullmatch(
            r"(?:(?:今天|昨天|前天|Today|Yesterday|星期[一二三四五六日天]|周[一二三四五六日天]|"
            r"Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s*)?"
            r"(?:(?:上午|下午|晚上|凌晨|中午)\s*)?\d{1,2}:\d{2}(?:\s*[AP]M)?"
            r"|今天|昨天|前天|Today|Yesterday|(?:星期|周)[一二三四五六日天]"
            r"|Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday"
            r"|(?:\d{4}[/-])?\d{1,2}[/-]\d{1,2}|\d{4}年\d{1,2}月\d{1,2}日(?:\s+\d{1,2}:\d{2})?",
            text.strip(), re.I))

    @staticmethod
    def _message_content_and_quote(raw):
        text = raw.strip()
        for pattern in [r"(?:^|\n)引用\s+(.+?)\s+的消息\s*[:\uff1a]\s*(.+)$",
                        r"(?:^|\n)Quote\s+(.+?)['\u2019]s message\s*:\s*(.+)$"]:
            match = re.search(pattern, text, re.S)
            if match and match[1].strip() and match[2].strip():
                return text[:match.start()].strip(), {"sender": match[1].strip(), "content": match[2].strip()}
        return text, None

    def _message_items(self, nodes, parent):
        items = self._children(nodes, parent)
        heights = [n["rect"]["height"] for n in items if n["rect"] and not self._looks_like_time(n["name"])]
        row_height = min(heights, default=max((n["rect"]["height"] for n in items if n["rect"]), default=0))
        result = []
        for node in items:
            content, quote = self._message_content_and_quote(node["name"])
            timestamp = self._looks_like_time(node["name"]) and node["rect"] and node["rect"]["height"] < row_height
            result.append({"path": node["path"], "kind": "timestamp" if timestamp else "text",
                           "content": content, "quote": quote, "raw_text": node["name"],
                           "class_name": node.get("class_name", ""), "role": node["role"], "rect": node["rect"]})
        return result

    def _remember(self, node):
        ref = node["ref"]
        name = self._session_name(node["name"])
        token = hashlib.sha256(f"{ref.bus}\0{ref.path}\0{name}".encode()).hexdigest()
        with self._paths_lock:
            self._paths[token] = node
            self._paths.move_to_end(token)
            while len(self._paths) > 2048:
                self._paths.popitem(last=False)
        return token

    def get_state(self, *, handle=None, max_nodes=5000):
        try:
            window = self._window(handle)
            nodes = self.adapter.window_nodes(window, max_nodes=max_nodes)
            sessions = self._list_node(nodes, CHAT_LIST_NAMES)
            messages = self._list_node(nodes, MESSAGE_LIST_NAMES)
            field = input_node(nodes)
            items = []
            for node in self._children(nodes, sessions):
                if node["role"] != "list item" or not node["name"]:
                    continue
                raw = node["name"]
                lines = [line.strip() for line in raw.splitlines()[1:]
                         if line.strip() and line.strip() not in {"消息免打扰", "Mute Notifications"}]
                pinned = bool(lines and lines[0] in {"已置顶", "Sticky on Top", "Pinned"})
                if pinned:
                    lines.pop(0)
                message_time = lines.pop() if lines and self._looks_like_time(lines[-1]) else ""
                unread = re.search(r"\[(\d+)(?:条)?\]", raw)
                items.append({
                    "path": self._remember(node), "name": self._session_name(raw), "raw_text": raw,
                    "preview": "\n".join(lines), "time": message_time,
                    "unread": int(unread[1]) if unread else 0, "rect": node["rect"],
                    "muted": any(s in raw for s in ["消息免打扰", "Mute Notifications"]),
                    "pinned": pinned,
                })
            return self._result(
                window=self._window_info(window), node_count=len(nodes),
                chat_list={"available": sessions is not None, "items": items,
                           "path": sessions["path"] if sessions else "", "rect": sessions["rect"] if sessions else None},
                chat_area={"available": messages is not None, "title": chat_title(nodes, window.title),
                           "path": messages["path"] if messages else "", "rect": messages["rect"] if messages else None,
                           "messages": self._message_items(nodes, messages)},
                input_box={"available": field is not None, **({k: field[k] for k in ["name", "path", "rect", "enabled", "focused"]}
                                                               if field else {})},
            )
        except Exception as exc:
            return {**self._error(str(exc)), "window": None, "chat_list": {"available": False, "items": []},
                    "chat_area": {"available": False, "messages": []}, "input_box": {"available": False}}

    def _activate(self, window):
        if not self.adapter._activate_window(window):
            raise RuntimeError("激活微信窗口失败")

    def _click(self, node, *, button=1, count=1):
        fresh = self.adapter.accessibility.info(node["ref"])
        if fresh["name"] != node["name"]:
            raise RuntimeError("控件已变化,请刷新后重试")
        rect = fresh["rect"]
        if rect is None or rect["width"] <= 0 or rect["height"] <= 0:
            raise RuntimeError("目标控件不可见")
        args = ["xdotool", "mousemove", str(rect["left"] + rect["width"] // 2),
                str(rect["top"] + rect["height"] // 2), "click", "--repeat", str(count), "--delay", "100", str(button)]
        if self.adapter._run_x11(args).returncode != 0:
            raise RuntimeError("点击微信控件失败")

    def _session(self, window, path):
        with self._paths_lock:
            previous = self._paths.get(path)
        if previous is None:
            raise RuntimeError("会话 path 已失效,请刷新列表")
        nodes = self.adapter.window_nodes(window)
        sessions = self._children(nodes, self._list_node(nodes, CHAT_LIST_NAMES))
        matches = [n for n in sessions if n["name"] and self._session_name(n["name"]) == self._session_name(previous["name"])
                   and n["ref"] == previous["ref"]]
        if len(matches) != 1:
            raise RuntimeError("会话列表已变化,请刷新后重试")
        return matches[0]

    def _select(self, window, node):
        name = self._session_name(node["name"])
        window.expected_chat = name
        nodes = self.adapter.window_nodes(window)
        self._require_unique_chat(name, nodes)
        if node["selected"] and chat_title(nodes, window.title) == name and input_node(nodes) and self.adapter._focus_input_area(window):
            return name
        fresh = self.adapter.accessibility.info(node["ref"])
        if self._session_name(fresh["name"]) != name:
            raise RuntimeError("会话已变化,请刷新后重试")
        self._click({**node, **fresh})
        deadline = time.monotonic() + 4
        while time.monotonic() < deadline:
            nodes = self.adapter.window_nodes(window)
            if chat_title(nodes, window.title) == name and input_node(nodes) and self.adapter._focus_input_area(window):
                return name
            time.sleep(.1)
        raise RuntimeError("会话已点击,但未能确认目标聊天与输入框")

    def _require_unique_chat(self, name, nodes):
        sessions = self._children(nodes, self._list_node(nodes, CHAT_LIST_NAMES))
        if name in self.adapter._ambiguous_titles or sum(self._session_name(n["name"]) == name for n in sessions if n["name"]) > 1:
            raise RuntimeError("存在同名会话,无法唯一确认发送目标")

    @operation
    def open_chat(self, *, path, max_nodes=5000):
        window = self._main()
        self._activate(window)
        node = self._session(window, path)
        self._select(window, node)
        return self.get_state(max_nodes=max_nodes)

    @operation
    def scroll_chats(self, *, direction, max_nodes=5000):
        if direction not in {"up", "down"}:
            raise ValueError("无效滚动方向")
        window = self._main()
        self._activate(window)
        self._scroll(window, direction)
        return self.get_state(max_nodes=max_nodes)

    def _scroll(self, window, direction):
        node = self._list_node(self.adapter.window_nodes(window), CHAT_LIST_NAMES)
        if node is None:
            raise RuntimeError("未找到会话列表")
        rect = node["rect"]
        result = self.adapter._run_x11([
            "xdotool", "mousemove", str(rect["left"] + rect["width"] // 2), str(rect["top"] + rect["height"] // 2),
            "click", "--repeat", "5", "--delay", "30", "4" if direction == "up" else "5",
        ])
        if result.returncode != 0:
            raise RuntimeError("滚动会话列表失败")
        time.sleep(.2)

    def send_message(self, *, message_type, message, filename, chat_name, max_nodes=5000, reply_to_message=None):
        if not self._console_lock.acquire(blocking=False):
            return self._error("Console 正在执行其他操作")
        try:
            if message_type == "voice":
                if not self.adapter.SUPPORTS_VOICE:
                    return self._error("Linux 语音需要 pulseaudio-utils 的 pactl 和 paplay")
                message, filename = synthesize_voice(message)
                message_type = "voice_file"
            with self.adapter.ui_operation_lock:
                return self._send_message(message_type=message_type, message=message, filename=filename,
                                          chat_name=chat_name, max_nodes=max_nodes, reply_to_message=reply_to_message)
        except Exception as exc:
            return self._error(str(exc))
        finally:
            self._console_lock.release()

    def _send_message(self, *, message_type, message, filename, chat_name, max_nodes, reply_to_message):
        window = self._main()
        self._activate(window)
        nodes = self.adapter.window_nodes(window)
        self._require_unique_chat(chat_name, nodes)
        if chat_title(nodes, window.title) != chat_name:
            raise RuntimeError("当前会话与发送目标不一致,停止发送")
        window.expected_chat = chat_name
        self._clear_reply(window)
        try:
            if reply_to_message:
                self._prepare_reply(window, reply_to_message)
            if message_type == "text":
                ok = self.adapter.send_text_to_window(window, message)
            elif message_type == "image":
                ok = self.adapter.send_image_to_window(window, message)
            elif message_type == "file":
                ok = self.adapter.send_file_to_window(window, message, filename)
            elif message_type in {"voice", "voice_file"}:
                if not self.adapter.SUPPORTS_VOICE:
                    raise RuntimeError("Linux 语音需要 pulseaudio-utils 的 pactl 和 paplay")
                ok = self.adapter.send_voice_to_window(window, message, filename)
            else:
                raise RuntimeError("不支持的消息类型")
        finally:
            if reply_to_message:
                with contextlib.suppress(Exception):
                    self._clear_reply(window)
        if not ok:
            raise RuntimeError("发送操作未完成,请通过聊天记录查证后再决定是否重试")
        return self.get_state(max_nodes=max_nodes)

    def _clear_reply(self, window):
        nodes = self.adapter.window_nodes(window)
        if window.expected_chat and chat_title(nodes, window.title) != window.expected_chat:
            raise RuntimeError("会话已变化,不清理引用")
        messages = self._list_node(nodes, MESSAGE_LIST_NAMES)
        if messages is None:
            return
        buttons = [n for n in nodes if n["role"] == "push button" and n["name"] in {"Delete quote", "删除引用", "取消引用"}
                   and n["rect"] and n["rect"]["top"] >= messages["rect"]["bottom"]
                   and n["rect"]["left"] >= messages["rect"]["left"]]
        if not buttons:
            return
        if len(buttons) != 1 or not self.adapter._active_window_matches(window):
            raise RuntimeError("无法安全清理引用预览")
        self._click(buttons[0])
        names = {node["name"] for node in self.adapter.window_nodes(window)}
        if buttons[0]["name"] in names:
            raise RuntimeError("引用预览尚未清理")

    def _prepare_reply(self, window, content):
        nodes = self.adapter.window_nodes(window)
        messages = self._list_node(nodes, MESSAGE_LIST_NAMES)
        if messages is None:
            raise RuntimeError("未找到聊天消息区")
        def normalize(text):
            return " ".join(text.split())
        descendants = [n for n in nodes if n["path"].startswith(messages["path"] + ".") and n["rect"]]
        matches = [n for n in descendants if n["name"] == content]
        if not matches:
            matches = [n for n in descendants if normalize(n["name"]) == normalize(content)]
        if not matches:
            raise RuntimeError("当前可见消息中未找到引用原文,未发送消息")
        try:
            self._quote_message(window, matches[-1], content)
        finally:
            self.adapter._focus_input_area(window)
        nodes = self.adapter.window_nodes(window)
        messages = self._list_node(nodes, MESSAGE_LIST_NAMES)
        if messages is None:
            raise RuntimeError("引用后未找到聊天区")
        previews = [n for n in nodes if n["role"] in {"label", "text"} and n["rect"]
                    and n["rect"]["top"] >= messages["rect"]["bottom"]
                    and normalize(content) in normalize(n["name"])]
        if not previews:
            raise RuntimeError("未能确认引用预览,未发送消息")

    def _quote_message(self, window, node, content):
        for fraction in [.5, .95, .05, .9, .1, .75, .25]:
            if not self.adapter._active_window_matches(window):
                raise RuntimeError("引用前目标窗口失去焦点")
            fresh = self.adapter.accessibility.info(node["ref"])
            rect = fresh["rect"]
            if fresh["name"] != node["name"] or not rect or rect["width"] <= 0 or rect["height"] <= 0:
                raise RuntimeError("引用原消息已变化")
            point = [str(int(rect["left"] + rect["width"] * fraction)), str(rect["top"] + rect["height"] // 2)]
            self.adapter._run_x11(["xdotool", "mousemove", *point, "click", "3"])
            time.sleep(.15)
            items = self._menu_nodes()
            copies = [n for n in items if n["name"] in {"Copy", "复制"}]
            quotes = [n for n in items if n["name"] in {"Quote", "Reply", "引用"}]
            if len(copies) != 1 or len(quotes) != 1:
                if items:
                    self.adapter._keys("Escape")
                continue
            owner = self.adapter._set_clipboard_text(f"awsl-quote-{time.monotonic_ns()}")
            if not owner:
                raise RuntimeError("无法验证引用正文")
            try:
                self._click(copies[0])
                copied = self.adapter._run_x11(["xclip", "-selection", "clipboard", "-o", "-t", "UTF8_STRING"])
                if copied.returncode != 0 or " ".join(copied.stdout.split()) != " ".join(content.split()):
                    raise RuntimeError("引用菜单对应的正文不一致,未发送消息")
            finally:
                self.adapter._stop_clipboard_owner(owner)
            fresh = self.adapter.accessibility.info(node["ref"])
            if not self.adapter._active_window_matches(window) or fresh["name"] != node["name"] or fresh["rect"] != rect:
                raise RuntimeError("引用原消息或焦点已变化")
            self.adapter._run_x11(["xdotool", "mousemove", *point, "click", "3"])
            self._menu({"引用", "Reply", "Quote"})
            return
        raise RuntimeError("未能定位原消息的引用菜单,未发送消息")

    def list_windows(self):
        try:
            windows = self._windows(main=True)
            return self._result(windows=[self._window_info(w) for w in windows])
        except Exception as exc:
            return self._error(str(exc))

    @staticmethod
    def _standalone_info(window):
        return {"name": window.title, "active": True,
                "debug_info": {"window_id": int(window.window_id, 16), "chat_id": window.talker}}

    def list_standalone_windows(self):
        try:
            return self._result(windows=[self._standalone_info(w) for w in self._windows()])
        except Exception as exc:
            return self._error(str(exc))

    def standalone_window_status(self, *, chat_id=None):
        try:
            return self._result(open=bool(chat_id and any(w.talker == chat_id for w in self._windows())))
        except Exception as exc:
            return self._error(str(exc))

    def _menu(self, name_options):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            candidates = [n for n in self._menu_nodes() if n["name"] in name_options]
            if len(candidates) == 1:
                self._click(candidates[0])
                return
            time.sleep(.1)
        raise RuntimeError("未找到唯一的目标菜单项")

    def _menu_nodes(self):
        return [node for frame in self.adapter.accessibility.frames() if frame["role"] == "popup menu"
                for node in self.adapter.accessibility.nodes(frame["ref"], max_nodes=200, max_depth=12)
                if node["role"] == "menu item"]

    def _open_standalone(self, window, node, chat_id=None):
        self._activate(window)
        name = self._session_name(node["name"])
        resolved = self.adapter._title_to_talker.get(name)
        self._require_unique_chat(name, self.adapter.window_nodes(window))
        if chat_id and resolved != chat_id:
            raise RuntimeError("会话 ID 与当前名称无法唯一对应,停止开窗")
        if any(w.title == name for w in self._windows()):
            self._select(window, node)
            return {"name": name, "conversation_id": resolved, "status": "already_open"}
        self._click(node, button=3)
        try:
            self._menu({"独立窗口显示", "Open in Separate Window", "Open in New Window", "Display in New Window"})
        finally:
            self._activate(window)
            self.adapter._run_x11(["xdotool", "mousemove", "--window", window.window_id, "150", "15", "click", "1"])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            matches = [w for w in self._windows() if w.title == name]
            if len(matches) == 1:
                self._select(window, node)
                return {"name": name, "conversation_id": resolved, "status": "opened"}
            time.sleep(.2)
        raise RuntimeError(f"等待独立窗口「{name}」超时")

    @operation
    def open_standalone_window(self, *, path, chat_id=None, max_nodes=5000):
        window = self._main()
        self._activate(window)
        result = self._open_standalone(window, self._session(window, path), chat_id)
        return self._result(**result, windows=self.list_standalone_windows().get("windows", []))

    @operation
    def close_standalone_window(self, *, window_id=None, chat_id=None):
        windows = [w for w in self._windows() if (window_id is None or int(w.window_id, 16) == window_id)
                   and (chat_id is None or w.talker == chat_id)]
        if len(windows) != 1:
            raise RuntimeError("无法唯一定位要关闭的独立窗口")
        window = windows[0]
        result = self.adapter._run_x11(["wmctrl", "-ic", window.window_id])
        if result.returncode != 0:
            raise RuntimeError("关闭独立窗口失败")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if all(w.window_id != window.window_id for w in self._windows()):
                return self._result(status="closed")
            time.sleep(.1)
        raise RuntimeError("独立窗口未关闭")

    @staticmethod
    def _cache_path():
        return Path.home() / ".config" / "awsl-wechat-gateway" / "standalone-windows.json"

    def get_cached_standalone_windows(self):
        path = self._cache_path()
        try:
            data = json.loads(path.read_text())
            if (not isinstance(data, dict) or data.get("version") != 2
                    or not isinstance(data.get("windows"), list) or len(data["windows"]) > 200):
                raise ValueError("独立窗口缓存格式不正确")
            for item in data["windows"]:
                if (not isinstance(item, dict) or not isinstance(item.get("name"), str)
                        or not isinstance(item.get("conversation_id"), str) or not item["conversation_id"].strip()):
                    raise ValueError("独立窗口缓存条目无效")
            return self._result(**data, exists=True, cache_path=str(path))
        except FileNotFoundError:
            return self._result(exists=False, windows=[], cache_path=str(path))
        except (OSError, ValueError) as exc:
            return self._error(str(exc))

    @operation
    def cache_standalone_windows(self):
        path = self._cache_path()
        windows = self._windows()
        if any(not w.talker for w in windows):
            raise RuntimeError("部分窗口缺少可验证的会话 ID,保留原缓存; 请确认 Chatlog 就绪且会话名称唯一")
        data = {"version": 2, "updated_at": datetime.now(UTC).isoformat(),
                "windows": [{"name": w.title, "conversation_id": w.talker} for w in windows]}
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(dir=path.parent)
        try:
            with os.fdopen(fd, "w") as file:
                json.dump(data, file, ensure_ascii=False)
            os.replace(temp, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp)
        return self.get_cached_standalone_windows()

    @operation
    def open_cached_standalone_windows(self, *, max_nodes=5000):
        cached = self.get_cached_standalone_windows()
        if not cached.get("available") or not cached.get("exists"):
            raise RuntimeError(cached.get("message") or "尚未保存独立窗口")
        window = self._main()
        self._activate(window)
        results = []
        for item in cached["windows"]:
            try:
                self._activate(window)
                chat_id = item.get("conversation_id")
                if not chat_id:
                    raise ValueError("缓存缺少会话 ID,请重新保存")
                self.adapter._refresh_group_cache(force=True)
                name = self.adapter._talker_to_title.get(chat_id)
                if not name:
                    raise ValueError("会话 ID 未找到当前显示名")
                previous = None
                for _ in range(80):
                    nodes = self.adapter.window_nodes(window)
                    items = self._children(nodes, self._list_node(nodes, CHAT_LIST_NAMES))
                    signature = tuple(n["name"] for n in items)
                    if signature == previous:
                        break
                    previous = signature
                    self._scroll(window, "up")
                previous = None
                for _ in range(80):
                    nodes = self.adapter.window_nodes(window)
                    items = self._children(nodes, self._list_node(nodes, CHAT_LIST_NAMES))
                    matches = [n for n in items if n["name"]
                               and self.adapter._title_to_talker.get(self._session_name(n["name"])) == chat_id]
                    if len(matches) > 1:
                        raise RuntimeError("会话列表中存在多个匹配项")
                    if len(matches) == 1:
                        results.append(self._open_standalone(window, matches[0], chat_id))
                        break
                    signature = tuple(n["name"] for n in items)
                    if signature == previous:
                        raise RuntimeError("会话列表中未找到缓存会话")
                    previous = signature
                    self._scroll(window, "down")
                else:
                    raise RuntimeError("搜索缓存会话超时")
            except Exception as exc:
                results.append({"name": item["name"], "status": "failed", "message": str(exc)})
        failed = sum(item["status"] == "failed" for item in results)
        return {
            **self._result(results=results, windows=self.list_standalone_windows().get("windows", [])),
            "available": failed == 0,
            "message": f"有 {failed} 个独立窗口恢复失败" if failed else "独立窗口缓存已恢复",
            "cached_count": len(cached["windows"]),
            "opened_count": sum(item["status"] == "opened" for item in results),
            "already_open_count": sum(item["status"] == "already_open" for item in results),
            "failed_count": failed,
        }

    def dump_window(self, *, handle, max_depth=5, path="", max_nodes=2000, include_patterns=False):
        try:
            window = self._window(handle)
            nodes = self.adapter.window_nodes(window, max_nodes=max_nodes)
            target = next((n for n in nodes if n["path"] == path), None)
            if target is None:
                raise ValueError("未找到指定控件 path")
            by_path = {}
            for node in nodes:
                if node["depth"] - target["depth"] > max_depth:
                    continue
                if node["path"] != path and not node["path"].startswith(path + "." if path else ""):
                    continue
                by_path[node["path"]] = {k: v for k, v in node.items() if k != "ref"}
                if include_patterns:
                    by_path[node["path"]].update(self.adapter.accessibility.debug_info(node["ref"]))
                by_path[node["path"]]["children"] = []
            for node_path, node in by_path.items():
                if node_path == path:
                    continue
                parent = node_path.rpartition(".")[0]
                if parent in by_path:
                    by_path[parent]["children"].append(node)
            return self._result(window=self._window_info(window), tree=by_path[path], node_count=len(by_path))
        except Exception as exc:
            return self._error(str(exc))
