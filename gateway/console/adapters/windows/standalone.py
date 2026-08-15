from __future__ import annotations

import contextlib
import logging
import os
import tempfile
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

from gateway.adapters.windows import WindowsGroupDebugInfo
from gateway.console.adapters.windows.constants import _CHAT_SCROLL_WHEEL_TIMES
from gateway.console.adapters.windows.models import (
    CachedStandaloneWindow,
    StandaloneWindowCache,
    WindowsStandaloneWindowInfo,
)

logger = logging.getLogger(__name__)


class WindowsStandaloneMixin:
    STANDALONE_WINDOW_CLASS: ClassVar[str] = "mmui::ChatSingleWindow"
    STANDALONE_WINDOW_TIMEOUT: ClassVar[float] = 5.0
    CHAT_LIST_TRAVERSAL_STEPS: ClassVar[int] = 80
    STANDALONE_CACHE_RELATIVE_PATH: ClassVar[Path] = (
        Path(".config") / "awsl-wechat-gateway" / "standalone-windows.json"
    )

    def list_standalone_windows(self) -> dict[str, Any]:
        auto = self._ensure_uia()
        return {
            "available": True,
            "platform": "windows",
            "windows": [
                self._standalone_window_info(control)
                for control in self._standalone_windows(auto)
            ],
        }

    def standalone_window_status(self, *, chat_id: str | None = None) -> dict[str, Any]:
        auto = self._ensure_uia()
        window = None if not chat_id else self._find_standalone_window_by_chat_id(auto, chat_id)
        return {
            "available": True,
            "platform": "windows",
            "open": window is not None,
        }

    def get_cached_standalone_windows(self) -> dict[str, Any]:
        cache_path = self._standalone_cache_path()
        try:
            cache = self._read_standalone_cache(cache_path)
        except FileNotFoundError:
            return {
                "available": True,
                "platform": "windows",
                "exists": False,
                "message": "尚未创建独立窗口缓存",
                "cache_path": str(cache_path),
                "windows": [],
            }
        except (OSError, ValueError) as e:
            return self._action_error(f"读取独立窗口缓存失败: {e}")
        return {
            "available": True,
            "platform": "windows",
            "exists": True,
            "message": f"缓存中有 {len(cache.windows)} 个独立窗口",
            "cache_path": str(cache_path),
            **cache.model_dump(mode="json"),
        }

    def cache_standalone_windows(self) -> dict[str, Any]:
        if not self._console_lock.acquire(blocking=False):
            return self._action_error("Console 正在执行其他操作")
        try:
            auto = self._ensure_uia()
            with self._ui_operation_lock():
                windows = [self._standalone_cache_entry(control) for control in self._standalone_windows(auto)]
                cache = StandaloneWindowCache(
                    updated_at=datetime.now(UTC),
                    windows=windows,
                )
                cache_path = self._standalone_cache_path()
                self._write_standalone_cache(cache_path, cache)
                return {
                    "available": True,
                    "platform": "windows",
                    "exists": True,
                    "message": f"已缓存 {len(windows)} 个独立窗口",
                    "cache_path": str(cache_path),
                    **cache.model_dump(mode="json"),
                }
        finally:
            self._console_lock.release()

    def open_cached_standalone_windows(self, *, max_nodes: int = 5000) -> dict[str, Any]:
        if not self._console_lock.acquire(blocking=False):
            return self._action_error("Console 正在执行其他操作")
        try:
            try:
                cached = self._read_standalone_cache(self._standalone_cache_path())
            except (OSError, ValueError) as e:
                return self._action_error(f"读取独立窗口缓存失败: {e}")

            cached_windows = cached.windows
            auto = self._ensure_uia()
            with self._ui_operation_lock():
                main_window = self._find_main_window(auto)
                if main_window is None:
                    return self._action_error("未找到微信主窗口")
                if not self._activate_window_locked(main_window):
                    return self._action_error("激活微信主窗口失败", window=main_window)

                results = []
                for cached_window in cached_windows:
                    results.append(
                        self._open_cached_standalone_window(
                            auto,
                            main_window,
                            cached_window,
                            max_nodes=max_nodes,
                        )
                    )

                windows = [
                    self._standalone_window_info(control)
                    for control in self._standalone_windows(auto)
                ]
                failed = sum(result["status"] == "failed" for result in results)
                return {
                    "available": failed == 0,
                    "platform": "windows",
                    "message": "独立窗口缓存已恢复" if failed == 0 else f"有 {failed} 个独立窗口恢复失败",
                    "cached_count": len(cached_windows),
                    "opened_count": sum(result["status"] == "opened" for result in results),
                    "already_open_count": sum(result["status"] == "already_open" for result in results),
                    "failed_count": failed,
                    "results": results,
                    "windows": windows,
                }
        finally:
            self._console_lock.release()

    def _open_cached_standalone_window(
        self,
        auto,
        main_window,
        cached_window: CachedStandaloneWindow,
        *,
        max_nodes: int,
    ) -> dict[str, Any]:
        name = cached_window.name.strip()
        conversation_id = cached_window.conversation_id
        result = {"name": name, "conversation_id": conversation_id}
        if not conversation_id:
            return {**result, "status": "failed", "message": "缓存缺少可验证的会话 ID"}

        existing = self._find_standalone_window_by_chat_id(auto, conversation_id)
        if existing is not None:
            return {**result, "status": "already_open", "message": "独立窗口已经打开"}

        if not self._activate_window_locked(main_window):
            return {**result, "status": "failed", "message": "激活微信主窗口失败"}
        target, error = self._find_cached_chat_session_cell(
            main_window,
            name=name,
            max_nodes=max_nodes,
        )
        if error:
            return {**result, "status": "failed", "message": error}
        if target is None:
            return {**result, "status": "failed", "message": "主窗口聊天列表中未找到缓存会话"}

        try:
            target.DoubleClick(waitTime=0.35)
        except Exception as e:
            return {**result, "status": "failed", "message": f"双击聊天项失败: {e}"}

        deadline = time.monotonic() + self.STANDALONE_WINDOW_TIMEOUT
        while True:
            standalone = self._find_standalone_window_by_chat_id(auto, conversation_id)
            if standalone is not None:
                return {**result, "status": "opened", "message": "独立窗口已打开"}
            if time.monotonic() >= deadline:
                break
            time.sleep(0.1)
        return {**result, "status": "failed", "message": "等待独立窗口出现超时"}

    def _find_cached_chat_session_cell(
        self,
        main_window,
        *,
        name: str,
        max_nodes: int,
    ) -> tuple[Any | None, str]:
        target, error = self._find_chat_session_cell_from_top(
            main_window,
            predicate=lambda node: self._chat_list_item(node)["name"] == name,
            max_nodes=max_nodes,
        )
        return (None if target is None else target["_control"], error)

    def _find_chat_session_cell_from_top(
        self,
        main_window,
        *,
        predicate: Callable[[dict[str, Any]], bool],
        max_nodes: int,
    ) -> tuple[dict[str, Any] | None, str]:
        nodes = self._collect_nodes(main_window, max_depth=18, max_nodes=max_nodes)
        list_node = self._chat_list_node(nodes)
        if list_node is None:
            return None, "未找到主窗口聊天列表"
        list_control = list_node["_control"]

        error = self._scroll_chat_list_to_edge(
            main_window,
            list_control,
            direction="up",
            max_nodes=max_nodes,
        )
        if error:
            return None, error

        previous_signature: tuple[tuple[str, str], ...] | None = None
        for _ in range(self.CHAT_LIST_TRAVERSAL_STEPS):
            nodes = self._collect_nodes(main_window, max_depth=18, max_nodes=max_nodes)
            cells = [node for node in nodes if node["class_name"] == "mmui::ChatSessionCell"]
            matches = [node for node in cells if predicate(node)]
            if len(matches) == 1:
                return matches[0], ""
            if len(matches) > 1:
                return None, "聊天列表中存在多个匹配会话，无法安全选择"

            signature = self._chat_session_signature(cells)
            if signature == previous_signature:
                return None, ""
            previous_signature = signature
            try:
                list_control.WheelDown(wheelTimes=_CHAT_SCROLL_WHEEL_TIMES, interval=0.03, waitTime=0.2)
            except Exception as e:
                return None, f"向下滚动聊天列表失败: {e}"
        return None, "未能确认到达聊天列表底部"

    def _scroll_chat_list_to_edge(
        self,
        main_window,
        list_control,
        *,
        direction: str,
        max_nodes: int,
    ) -> str:
        previous_signature: tuple[tuple[str, str], ...] | None = None
        for _ in range(self.CHAT_LIST_TRAVERSAL_STEPS):
            nodes = self._collect_nodes(main_window, max_depth=18, max_nodes=max_nodes)
            cells = [node for node in nodes if node["class_name"] == "mmui::ChatSessionCell"]
            if not cells:
                return "聊天列表中没有可见会话"
            signature = self._chat_session_signature(cells)
            if signature == previous_signature:
                return ""
            previous_signature = signature
            try:
                scroll = list_control.WheelUp if direction == "up" else list_control.WheelDown
                scroll(wheelTimes=_CHAT_SCROLL_WHEEL_TIMES, interval=0.03, waitTime=0.2)
            except Exception as e:
                return f"滚动聊天列表失败: {e}"
        edge = "顶部" if direction == "up" else "底部"
        return f"未能确认到达聊天列表{edge}"

    def _chat_session_signature(
        self,
        cells: list[dict[str, Any]],
    ) -> tuple[tuple[str, str], ...]:
        return tuple(
            (node["path"], self._chat_list_item(node)["name"])
            for node in cells
        )

    def open_standalone_window(
        self,
        *,
        path: str,
        chat_id: str | None = None,
        max_nodes: int = 5000,
    ) -> dict[str, Any]:
        if not self._console_lock.acquire(blocking=False):
            return self._action_error("Console 正在执行其他操作")
        try:
            return self._open_standalone_window(path=path, chat_id=chat_id, max_nodes=max_nodes)
        finally:
            self._console_lock.release()

    def _open_standalone_window(
        self,
        *,
        path: str,
        chat_id: str | None,
        max_nodes: int,
    ) -> dict[str, Any]:
        auto = self._ensure_uia()
        with self._ui_operation_lock():
            main_window = self._find_main_window(auto)
            if main_window is None:
                return self._action_error("未找到微信主窗口")

            try:
                path_indexes = self._parse_path(path)
            except ValueError:
                return self._action_error(f"无效 path={path}", window=main_window)

            target = self._find_by_path(main_window, path_indexes)
            if target is None:
                return self._action_error(f"未找到聊天项 path={path}", window=main_window)
            target_info = self._control_info(target)
            if target_info["class_name"] != "mmui::ChatSessionCell":
                return self._action_error(f"path={path} 不是 ChatSessionCell", window=main_window)

            target_automation_id = target_info["automation_id"]
            expected_chat_name = self._chat_name_from_raw(target_info["name"])
            if isinstance(target_automation_id, str):
                expected_chat_name = (
                    target_automation_id.removeprefix("session_item_")
                    or expected_chat_name
                )
            if not expected_chat_name:
                return self._action_error(f"聊天项缺少名称 path={path}", window=main_window)

            existing = self._find_standalone_window(
                auto,
                chat_id=chat_id,
                chat_name=expected_chat_name,
            )
            if existing is not None:
                return {
                    "available": True,
                    "already_open": True,
                    "message": "独立窗口已经打开",
                    "window": self._standalone_window_info(existing),
                }

            if not self._activate_window_locked(main_window):
                return self._action_error("激活微信主窗口失败", window=main_window)

            target_node, error = self._find_chat_session_cell_from_top(
                main_window,
                predicate=lambda node: (
                    self._chat_list_item(node)["name"] == expected_chat_name
                ),
                max_nodes=max_nodes,
            )
            if error:
                return self._action_error(error, window=main_window)
            if target_node is None:
                return self._action_error(
                    f"从顶部向下扫描后未找到聊天项「{expected_chat_name}」",
                    window=main_window,
                )

            try:
                target_node["_control"].DoubleClick(waitTime=0.35)
            except Exception as e:
                return self._action_error(f"双击聊天项失败: {e}", window=main_window)

            deadline = time.monotonic() + self.STANDALONE_WINDOW_TIMEOUT
            while True:
                standalone = self._find_standalone_window(
                    auto,
                    chat_id=chat_id,
                    chat_name=expected_chat_name,
                )
                if standalone is not None:
                    return {
                        "available": True,
                        "already_open": False,
                        "message": "独立窗口已打开",
                        "window": self._standalone_window_info(standalone),
                    }
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.1)
            return self._action_error(f"等待独立窗口「{expected_chat_name}」出现超时", window=main_window)

    def close_standalone_window(
        self,
        *,
        window_id: int | None = None,
        chat_id: str | None = None,
    ) -> dict[str, Any]:
        if not self._console_lock.acquire(blocking=False):
            return self._action_error("Console 正在执行其他操作")
        try:
            return self._close_standalone_window(window_id=window_id, chat_id=chat_id)
        finally:
            self._console_lock.release()

    def _close_standalone_window(
        self,
        *,
        window_id: int | None,
        chat_id: str | None,
    ) -> dict[str, Any]:
        auto = self._ensure_uia()
        with self._ui_operation_lock():
            window = (
                self._find_window_by_handle(auto, window_id)
                if window_id is not None
                else self._find_standalone_window_by_chat_id(auto, chat_id or "")
            )
            if window is None:
                return {
                    "available": True,
                    "already_closed": True,
                    "message": "独立窗口已经关闭",
                    "window": None,
                }

            info = self._control_info(window)
            if info["class_name"] != self.STANDALONE_WINDOW_CLASS:
                return self._action_error(f"window_id={window_id} 不是独立聊天窗口")
            conversation_id = self._wechat_adapter.standalone_conversation_id(
                info["automation_id"]
            )
            window_id = info["native_window_handle"]
            window_info = self._standalone_window_info(window)

            close_requested = False
            try:
                pattern = window.GetWindowPattern()
                if pattern is not None:
                    pattern.Close()
                    close_requested = True
            except Exception as e:
                logger.debug("UIA Close 独立窗口失败,尝试 WM_CLOSE: %s", e)

            if not close_requested:
                if not window_id:
                    return self._action_error("UIA Close 失败且独立聊天窗口缺少 window_id")
                try:
                    import win32con
                    import win32gui

                    win32gui.PostMessage(window_id, win32con.WM_CLOSE, 0, 0)
                except Exception as e:
                    return self._action_error(f"关闭独立窗口失败: {e}")

            deadline = time.monotonic() + self.STANDALONE_WINDOW_TIMEOUT
            while True:
                if window_id:
                    closed = self._find_window_by_handle(auto, window_id) is None
                elif conversation_id:
                    closed = self._find_standalone_window_by_chat_id(auto, conversation_id) is None
                else:
                    try:
                        closed = not window.Exists(0)
                    except Exception:
                        closed = False
                if closed:
                    return {
                        "available": True,
                        "already_closed": False,
                        "message": "独立窗口已关闭",
                        "window": window_info,
                    }
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.1)
            return {
                "available": False,
                "platform": "windows",
                "message": "等待独立窗口关闭超时",
                "window": window_info,
            }

    def _standalone_window_info(self, control) -> dict[str, Any]:
        return self._standalone_window_snapshot(control).model_dump()

    def _standalone_window_snapshot(self, control) -> WindowsStandaloneWindowInfo:
        info = self._control_info(control)
        return WindowsStandaloneWindowInfo(
            name=info["name"],
            active=True,
            debug_info=WindowsGroupDebugInfo(
                window_id=info["native_window_handle"] or None,
                automation_id=info["automation_id"] or None,
            ),
        )

    def _standalone_cache_entry(self, control) -> CachedStandaloneWindow:
        info = self._control_info(control)
        return CachedStandaloneWindow(
            name=info["name"],
            conversation_id=self._wechat_adapter.standalone_conversation_id(info["automation_id"]),
        )

    def _find_standalone_window_by_name(self, auto, name: str):
        matches = []
        for control in self._standalone_windows(auto):
            info = self._control_info(control)
            if self._chat_names_match(name, info["name"], []):
                matches.append(control)
        return matches[0] if len(matches) == 1 else None

    def _find_standalone_window(
        self,
        auto,
        *,
        chat_id: str | None,
        chat_name: str,
    ):
        if chat_id:
            window = self._find_standalone_window_by_chat_id(auto, chat_id)
            if window is not None:
                return window
        return self._find_standalone_window_by_name(auto, chat_name)

    def _find_standalone_window_by_chat_id(self, auto, chat_id: str):
        for control in self._standalone_windows(auto):
            automation_id = self._control_info(control)["automation_id"]
            conversation_id = self._wechat_adapter.standalone_conversation_id(automation_id)
            if conversation_id == chat_id:
                return control
        return None

    def _standalone_windows(self, auto) -> list[Any]:
        return [
            control
            for control in self._iter_desktop_windows(auto)
            if self._control_info(control)["class_name"] == self.STANDALONE_WINDOW_CLASS
        ]

    @classmethod
    def _standalone_cache_path(cls) -> Path:
        return Path.home() / cls.STANDALONE_CACHE_RELATIVE_PATH

    @staticmethod
    def _write_standalone_cache(cache_path: Path, cache: StandaloneWindowCache) -> None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix="standalone-windows-", suffix=".tmp", dir=cache_path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(cache.model_dump_json(indent=2))
                f.write("\n")
            os.replace(temp_path, cache_path)
            with contextlib.suppress(OSError):
                cache_path.chmod(0o600)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp_path)

    @staticmethod
    def _read_standalone_cache(cache_path: Path) -> StandaloneWindowCache:
        return StandaloneWindowCache.model_validate_json(cache_path.read_text(encoding="utf-8"))
