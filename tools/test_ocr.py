"""macOS OCR 测试脚本 - 打印所有群的 OCR 原始数据和合并后消息"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from gateway.utils.ocr import screenshot_and_ocr, get_others_messages
from gateway.adapters.macos import MacOSWeChatAdapter

adapter = MacOSWeChatAdapter()
windows = adapter.find_all_wechat_windows()
print(f"发现 {len(windows)} 个窗口\n")

for w in windows:
    title = w["title"]
    wid = w["window"].window_id
    results = screenshot_and_ocr(wid)

    print(f"{'=' * 60}")
    print(f" {title} (wid={wid})")
    print(f"{'=' * 60}")

    # 原始 OCR 数据
    print(f"\n  --- 原始 OCR ({len(results)} 条) ---")
    prev_y = None
    for r in results:
        gap = f"  gap={prev_y - r['y']:.4f}" if prev_y is not None else ""
        side = "L" if r["x"] < 0.5 else "R"
        print(f"  {side} x={r['x']:.3f} y={r['y']:.3f} "
              f"conf={r['confidence']:.2f}{gap}  \"{r['text']}\"")
        prev_y = r["y"]

    # 合并后消息
    messages = get_others_messages(results)
    print(f"\n  --- 合并后消息 ({len(messages)} 条) ---")
    for i, m in enumerate(messages):
        print(f"  [{i}] {m}")

    print()
