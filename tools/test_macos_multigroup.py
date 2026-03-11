#!/usr/bin/env python3
"""macOS 多群模式测试 - 枚举窗口 + 读取消息"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from gateway.adapters.macos import MacOSWeChatAdapter


def main():
    adapter = MacOSWeChatAdapter()

    # 枚举窗口
    windows = adapter.find_all_wechat_windows()
    print(f"发现 {len(windows)} 个聊天窗口:")
    for w in windows:
        print(f"  - \"{w['title']}\" (index={w['window'].index})")

    if not windows:
        print("没有聊天窗口，请先双击打开一个群聊")
        return

    # 逐个读取消息
    for w in windows:
        title = w["title"]
        win = w["window"]
        print(f"\n--- [{title}] 读取消息 ---")
        msgs = adapter.get_messages_from_window(win)
        print(f"获取到 {len(msgs)} 条消息")
        for m in msgs[-5:]:
            print(f"  {m[:80]}")

    # 发送测试（可选，传 --send 参数）
    if "--send" in sys.argv:
        for w in windows:
            title = w["title"]
            win = w["window"]
            print(f"\n向 \"{title}\" 发送测试消息...")
            ok = adapter.send_text_to_window(win, f"[测试] 多群模式 - {title}")
            print(f"  发送结果: {ok}")


if __name__ == "__main__":
    main()
