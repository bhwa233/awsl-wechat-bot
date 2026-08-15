"""Windows 微信语音发送脚本。"""

import argparse
import base64
import sys
import time
from pathlib import Path

import uiautomation as auto

from gateway.adapters.windows import WindowsWeChatAdapter
from gateway.utils.voice_synthesis import synthesize_voice


def find_window(adapter: WindowsWeChatAdapter, group: str):
    return next(
        (item.window for item in adapter.find_all_wechat_windows() if item.title == group),
        None,
    )


def get_voice_items(window) -> list[tuple[tuple[int, ...], str]]:
    return [
        (tuple(control.GetRuntimeId()), control.Name)
        for control, _depth in auto.WalkControl(window, maxDepth=15)
        if control.ClassName == "mmui::ChatVoiceItemView" and control.Exists(0)
    ]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--file-path")
    parser.add_argument("--send", action="store_true")
    return parser.parse_args()


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    args = parse_args()
    if not args.send:
        print("未指定 --send, 跳过微信发送", flush=True)
        return

    adapter = WindowsWeChatAdapter()
    window = find_window(adapter, args.group)
    if window is None:
        raise RuntimeError(f"未找到微信窗口: {args.group}")
    import win32gui

    print(
        f"target_hwnd={window.NativeWindowHandle} foreground_hwnd={win32gui.GetForegroundWindow()}",
        flush=True,
    )
    before = get_voice_items(window)
    before_ids = {runtime_id for runtime_id, _name in before}
    filename = None
    message = args.text
    if args.file_path:
        path = Path(args.file_path)
        if not path.is_file():
            raise RuntimeError(f"音频文件不存在: {path}")
        filename = path.name
        message = base64.b64encode(path.read_bytes()).decode("ascii")
    if not filename:
        message, filename = synthesize_voice(message)
    sent = adapter.send_voice_to_window(window, message, filename)
    if not sent:
        print(f"foreground_after={win32gui.GetForegroundWindow()}", flush=True)
        raise RuntimeError("语音发送失败")
    deadline = time.monotonic() + 10
    refreshed_window = find_window(adapter, args.group)
    if refreshed_window is None:
        raise RuntimeError(f"发送后未找到微信窗口: {args.group}")
    after = get_voice_items(refreshed_window)
    while not ({runtime_id for runtime_id, _name in after} - before_ids) and time.monotonic() < deadline:
        time.sleep(0.2)
        refreshed_window = find_window(adapter, args.group)
        if refreshed_window is None:
            continue
        after = get_voice_items(refreshed_window)
    new_items = [item for item in after if item[0] not in before_ids]
    if not new_items:
        raise RuntimeError(f"松手后未出现新语音气泡: before={before!r}, after={after!r}")
    print(f"sent group={args.group!r} voice={new_items[-1][1]!r}", flush=True)


if __name__ == "__main__":
    main()
