from __future__ import annotations

from typing import Any

from gateway.console.adapters.windows.constants import _STATE_MAX_DEPTH


class WindowsStateMixin:
    def get_state(self, *, handle: int | None = None, max_nodes: int = 5000) -> dict[str, Any]:
        auto = self._ensure_uia()
        window = self._resolve_window(auto, handle)
        if window is None:
            message = f"未找到窗口 handle={handle}" if handle else "未找到微信主窗口"
            return {
                "available": False,
                "platform": "windows",
                "message": message,
                "window": None,
                "chat_list": {"available": False, "items": []},
                "chat_area": {"available": False, "messages": []},
                "input_box": {"available": False},
            }

        info = self._control_info(window)
        if not self._is_wechat_window(info):
            return {
                "available": False,
                "platform": "windows",
                "message": f"handle={handle} 不是微信窗口",
                "window": info,
                "chat_list": {"available": False, "items": []},
                "chat_area": {"available": False, "messages": []},
                "input_box": {"available": False},
            }

        nodes = self._collect_nodes(window, max_depth=_STATE_MAX_DEPTH, max_nodes=max_nodes)
        chat_list = self._parse_chat_list(nodes)
        return {
            "available": True,
            "platform": "windows",
            "message": "",
            "window": info,
            "chat_list": chat_list,
            "chat_area": self._parse_chat_area(nodes, chat_list["items"]),
            "input_box": self._parse_input_box(nodes),
            "node_count": len(nodes),
        }

    def dump_window(
        self,
        *,
        handle: int,
        max_depth: int = 5,
        path: str = "",
        max_nodes: int = 2000,
        include_patterns: bool = False,
    ) -> dict[str, Any]:
        auto = self._ensure_uia()
        window = self._find_window_by_handle(auto, handle)
        if window is None:
            return {
                "available": False,
                "platform": "windows",
                "message": f"未找到窗口 handle={handle}",
                "window": None,
                "tree": None,
            }

        info = self._control_info(window)
        if not self._is_wechat_window(info):
            return {
                "available": False,
                "platform": "windows",
                "message": f"handle={handle} 不是微信窗口",
                "window": info,
                "tree": None,
            }

        try:
            path_indexes = self._parse_path(path)
        except ValueError:
            return {
                "available": False,
                "platform": "windows",
                "message": f"无效 path={path}",
                "window": info,
                "tree": None,
            }

        target = self._find_by_path(window, path_indexes)
        if target is None:
            return {
                "available": False,
                "platform": "windows",
                "message": f"未找到子节点 path={path}",
                "window": info,
                "tree": None,
            }

        counter = {"count": 0}
        return {
            "available": True,
            "platform": "windows",
            "message": "",
            "window": info,
            "tree": self._dump_control(
                target,
                max_depth=max_depth,
                depth=0,
                path=path_indexes,
                counter=counter,
                max_nodes=max_nodes,
                include_patterns=include_patterns,
            ),
            "node_count": counter["count"],
        }

    def _parse_chat_list(self, nodes: list[dict[str, Any]]) -> dict[str, Any]:
        list_node = self._chat_list_node(nodes)
        items = [
            self._chat_list_item(node)
            for node in nodes
            if node["class_name"] == "mmui::ChatSessionCell" or node["automation_id"].startswith("session_item_")
        ]
        return {
            "available": list_node is not None,
            "path": "" if list_node is None else list_node["path"],
            "rect": None if list_node is None else list_node["rect"],
            "items": items,
        }

    @staticmethod
    def _chat_list_node(nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
        return WindowsStateMixin._first_node(
            nodes,
            lambda node: node["control_type"] == "ListControl"
            and (
                node["automation_id"] == "session_list"
                or (node["class_name"] == "mmui::XTableView" and node["name"] == "会话")
            ),
        )

    def _parse_chat_area(
        self,
        nodes: list[dict[str, Any]],
        chat_items: list[dict[str, Any]],
    ) -> dict[str, Any]:
        message_list = self._first_node(
            nodes,
            lambda node: node["control_type"] == "ListControl"
            and (node["automation_id"] == "chat_message_list" or node["name"] == "消息"),
        )
        messages = []
        if message_list is not None:
            message_prefix = message_list["path"] + "."
            messages = [
                self._message_item(node)
                for node in nodes
                if node["path"].startswith(message_prefix) and node["depth"] == message_list["depth"] + 1
            ]

        detail_node = self._first_node(
            nodes,
            lambda node: node["class_name"] in {"mmui::ChatDetailView", "mmui::ChatMessagePage", "mmui::ChatPage"},
        )
        title_node = self._chat_title_node(nodes)
        raw_title = "" if title_node is None else str(title_node["name"]).strip()
        chat_names = [item["name"] for item in chat_items]
        if raw_title in chat_names:
            title = raw_title
        else:
            title = next(
                (name for name in chat_names if self._chat_names_match(name, raw_title, chat_names)),
                raw_title,
            )
        return {
            "available": message_list is not None,
            "title": title,
            "path": "" if message_list is None else message_list["path"],
            "rect": None if message_list is None else message_list["rect"],
            "detail_path": None if detail_node is None else detail_node["path"],
            "messages": messages,
        }

    @staticmethod
    def _chat_title_node(nodes: list[dict[str, Any]]) -> dict[str, Any] | None:
        return WindowsStateMixin._first_node(
            nodes,
            lambda node: node["control_type"] == "TextControl"
            and node["automation_id"].endswith("big_title_line_h_view"),
        )

    def _parse_input_box(self, nodes: list[dict[str, Any]]) -> dict[str, Any]:
        input_node = self._first_node(
            nodes,
            lambda node: node["control_type"] == "EditControl"
            and (node["automation_id"] == "chat_input_field" or node["class_name"] == "mmui::ChatInputField"),
        )
        if input_node is None:
            return {"available": False}

        return {
            "available": True,
            "path": input_node["path"],
            "name": input_node["name"],
            "rect": input_node["rect"],
            "enabled": input_node["enabled"],
            "focused": input_node["focused"],
        }

    def _chat_list_item(self, node: dict[str, Any]) -> dict[str, Any]:
        raw_text = node["name"]
        chat_name = node["automation_id"].removeprefix("session_item_") or self._chat_name_from_raw(raw_text)
        unread = self._unread_count(raw_text)
        preview, message_time, pinned = self._preview_from_raw(raw_text, chat_name)
        return {
            "path": node["path"],
            "name": chat_name,
            "raw_text": raw_text,
            "preview": preview,
            "time": message_time,
            "unread": unread,
            "muted": "消息免打扰" in raw_text,
            "pinned": pinned,
            "rect": node["rect"],
        }

    def _message_item(self, node: dict[str, Any]) -> dict[str, Any]:
        raw_text = node["name"]
        kind = "timestamp" if node["class_name"] == "mmui::ChatItemView" else "text"
        content, quote = self._message_content_and_quote(raw_text)
        return {
            "path": node["path"],
            "kind": kind,
            "content": content,
            "quote": quote,
            "raw_text": raw_text,
            "class_name": node["class_name"],
            "rect": node["rect"],
        }

    @staticmethod
    def _first_node(nodes: list[dict[str, Any]], predicate) -> dict[str, Any] | None:
        for node in nodes:
            if predicate(node):
                return node
        return None

    @staticmethod
    def _chat_name_from_raw(raw_text: str) -> str:
        raw_text = raw_text.strip()
        if not raw_text:
            return ""
        if " [" in raw_text:
            return raw_text.split(" [", 1)[0].strip()
        return raw_text.split("  ", 1)[0].strip()

    @staticmethod
    def _unread_count(raw_text: str) -> int:
        import re

        match = re.search(r"\[(\d+)条\]", raw_text)
        if not match:
            return 0
        return int(match.group(1))

    @staticmethod
    def _preview_from_raw(raw_text: str, chat_name: str) -> tuple[str, str, bool]:
        preview = raw_text.removeprefix(chat_name).strip()
        if preview.startswith("[") and "条]" in preview:
            preview = preview.split("条]", 1)[1].strip()
        preview = preview.replace("消息免打扰", "").strip()
        lines = [line.strip() for line in preview.splitlines()]
        pinned = bool(lines and lines[0] == "已置顶")
        if pinned:
            lines.pop(0)
        while lines and not lines[-1]:
            lines.pop()
        if not lines:
            return "", "", pinned

        if not WindowsStateMixin._looks_like_chat_time(lines[-1]):
            return "\n".join(lines).strip(), "", pinned

        message_time = lines.pop()
        return "\n".join(lines).strip(), message_time, pinned

    @staticmethod
    def _looks_like_chat_time(value: str) -> bool:
        import re

        text = value.strip()
        if not text:
            return False
        if re.fullmatch(r"(?:(?:今天|昨天|前天)\s+)?(上午|下午|晚上|凌晨|中午)?\s*\d{1,2}:\d{2}", text):
            return True
        if text in {"昨天", "前天"}:
            return True
        if re.fullmatch(r"(星期|周)[一二三四五六日天]", text):
            return True
        return bool(re.fullmatch(r"(\d{4}[/-])?\d{1,2}[/-]\d{1,2}", text))

    @staticmethod
    def _message_content_and_quote(raw_text: str) -> tuple[str, dict[str, str] | None]:
        import re

        text = raw_text.strip()
        match = re.search(r"引用\s+(.+?)\s+的消息\s*[:：]\s*(.+)$", text, re.S)
        if match is None:
            return text, None

        content = text[: match.start()].strip()
        quote_sender = match.group(1).strip()
        quote_content = match.group(2).strip()
        if not quote_sender or not quote_content:
            return text, None

        return content, {
            "sender": quote_sender,
            "content": quote_content,
        }
