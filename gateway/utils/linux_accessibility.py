from __future__ import annotations

import contextlib
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path

ACCESSIBLE = "org.a11y.atspi.Accessible"
COMPONENT = "org.a11y.atspi.Component"
ROOT = "/org/a11y/atspi/accessible/root"


@dataclass(frozen=True)
class AccessibleRef:
    bus: str
    path: str


class LinuxAccessibility:
    def __init__(self):
        self._local = threading.local()

    def _connect(self):
        from jeepney import DBusAddress, new_method_call
        from jeepney.io.blocking import open_dbus_connection
        from jeepney.wrappers import unwrap_msg

        address = os.environ.get("AT_SPI_BUS_ADDRESS")
        if not address:
            session = os.environ.get("DBUS_SESSION_BUS_ADDRESS")
            for process in Path("/proc").iterdir():
                if not process.name.isdigit():
                    continue
                try:
                    if process.stat().st_uid != os.getuid() or (process / "comm").read_text().strip() not in {"wechat", "weixin"}:
                        continue
                    env = dict(item.split(b"=", 1) for item in (process / "environ").read_bytes().split(b"\0") if b"=" in item)
                    session = env.get(b"DBUS_SESSION_BUS_ADDRESS", b"").decode() or session
                except OSError:
                    continue
                if session:
                    break
            with open_dbus_connection(session or "SESSION") as connection:
                message = new_method_call(DBusAddress("/org/a11y/bus", "org.a11y.Bus", "org.a11y.Bus"), "GetAddress")
                address, = unwrap_msg(connection.send_and_get_reply(message, timeout=3))
        return open_dbus_connection(address)

    def call(self, ref: AccessibleRef, interface: str, method: str, signature=None, body=()):
        from jeepney import DBusAddress, new_method_call
        from jeepney.wrappers import unwrap_msg

        connection = getattr(self._local, "connection", None)
        if connection is None:
            connection = self._local.connection = self._connect()
        try:
            message = new_method_call(DBusAddress(ref.path, ref.bus, interface), method, signature, body)
            return unwrap_msg(connection.send_and_get_reply(message, timeout=2))
        except Exception:
            connection.close()
            self._local.connection = None
            raise

    def children(self, ref: AccessibleRef) -> list[AccessibleRef]:
        items, = self.call(ref, ACCESSIBLE, "GetChildren")
        return [AccessibleRef(bus, path) for bus, path in items if path != "/org/a11y/atspi/null"]

    def info(self, ref: AccessibleRef) -> dict:
        (_, name), = self.call(ref, "org.freedesktop.DBus.Properties", "Get", "ss", (ACCESSIBLE, "Name"))
        role, = self.call(ref, ACCESSIBLE, "GetRoleName")
        rect = None
        with contextlib.suppress(Exception):
            (x, y, width, height), = self.call(ref, COMPONENT, "GetExtents", "u", (0,))
            rect = {"left": x, "top": y, "right": x + width, "bottom": y + height, "width": width, "height": height}
        states = 0
        with contextlib.suppress(Exception):
            words, = self.call(ref, ACCESSIBLE, "GetState")
            states = sum(word << (32 * i) for i, word in enumerate(words))
        return {
            "ref": ref,
            "name": name,
            "role": role,
            "rect": rect,
            "focused": bool(states & (1 << 12)),
            "selected": bool(states & (1 << 23)),
            "enabled": bool(states & (1 << 8)),
        }

    def application(self) -> AccessibleRef:
        registry = AccessibleRef("org.a11y.atspi.Registry", ROOT)
        for ref in self.children(registry):
            with contextlib.suppress(Exception):
                if self.info(ref)["name"].lower() in {"wechat", "weixin"}:
                    return ref
        raise RuntimeError("微信未注册 AT-SPI,请带 QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1 重启微信")

    def frames(self) -> list[dict]:
        return [self.info(ref) for ref in self.children(self.application())]

    def frame(self, title: str, geometry: tuple[int, int, int, int]) -> AccessibleRef:
        x, y, width, _height = geometry
        candidates = []
        for node in self.frames():
            rect = node["rect"]
            if node["name"] != title or rect is None:
                continue
            if abs(rect["left"] - x) <= 8 and abs(rect["top"] - y) <= 40 and abs(rect["width"] - width) <= 16:
                candidates.append(node["ref"])
        if len(candidates) != 1:
            raise RuntimeError(f"无法唯一定位微信窗口「{title}」")
        return candidates[0]

    def nodes(self, root: AccessibleRef, *, max_nodes=5000, max_depth=40, visible_only=True) -> list[dict]:
        result = []
        pending = [(root, "", 0)]
        seen = set()
        deadline = time.monotonic() + 8
        while pending and len(result) < max_nodes:
            if time.monotonic() >= deadline:
                raise RuntimeError("AT-SPI 控件树读取超时")
            ref, path, depth = pending.pop()
            if ref in seen:
                continue
            seen.add(ref)
            node = self.info(ref)
            rect = node["rect"]
            if visible_only and depth > 0 and rect is not None and (rect["width"] <= 0 or rect["height"] <= 0):
                continue
            result.append({**node, "path": path, "depth": depth})
            if depth >= max_depth:
                continue
            children = self.children(ref)
            pending.extend((child, f"{path}.{i}".lstrip("."), depth + 1) for i, child in reversed(list(enumerate(children))))
        return result

    def focus(self, ref: AccessibleRef) -> bool:
        focused, = self.call(ref, COMPONENT, "GrabFocus")
        return focused

    def text(self, ref: AccessibleRef) -> str:
        text, = self.call(ref, "org.a11y.atspi.Text", "GetText", "ii", (0, -1))
        return text

    def debug_info(self, ref: AccessibleRef) -> dict:
        interfaces, = self.call(ref, ACCESSIBLE, "GetInterfaces")
        attributes, = self.call(ref, ACCESSIBLE, "GetAttributes")
        patterns = {}
        for interface in interfaces:
            name = interface.rsplit(".", 1)[-1]
            if name == "Accessible":
                continue
            values = {"interface": interface}
            if name == "Action":
                actions, = self.call(ref, interface, "GetActions")
                values["actions"] = [{"name": action[0], "description": action[1], "key_binding": action[2]}
                                     for action in actions]
            if name == "Text":
                values["text"] = self.text(ref)
            patterns[name.lower()] = values
        return {"extra": {"interfaces": interfaces, "attributes": attributes}, "patterns": patterns}


def input_node(nodes: list[dict]) -> dict | None:
    candidates = [n for n in nodes if n["role"] == "text" and n["name"] not in {"搜索", "Search"}
                  and n["rect"] and n["rect"]["width"] > 0 and n["rect"]["height"] > 0]
    return max(candidates, key=lambda n: n["rect"]["top"], default=None)


def chat_title(nodes: list[dict], window_title: str) -> str:
    if window_title not in {"微信", "WeChat", "Weixin"}:
        return window_title
    field = input_node(nodes)
    if field is None:
        return ""
    if field["name"] not in {"输入", "Input", "Enter", "输入消息", "Message"}:
        return field["name"]
    sessions = [n["name"].splitlines()[0] for n in nodes if n["role"] == "list item" and n["name"]]
    titles = {n["name"] for n in nodes if n["role"] == "label"}
    matches = [name for name in sessions if name in titles]
    if len(matches) == 1:
        return matches[0]
    messages = next((n for n in nodes if n["role"] == "list" and n["name"] in {"消息", "Messages"} and n["rect"]), None)
    frame = nodes[0]["rect"] if nodes else None
    if messages is None or frame is None:
        return ""
    labels = [n for n in nodes if n["role"] == "label" and n["name"] and n["rect"]
              and n["rect"]["left"] >= messages["rect"]["left"]
              and frame["top"] + 35 <= n["rect"]["top"] < messages["rect"]["top"]
              and not n["name"].strip("()\uff08\uff09").isdigit()]
    labels.sort(key=lambda n: (n["rect"]["top"], -n["depth"]))
    return labels[0]["name"] if labels else ""
