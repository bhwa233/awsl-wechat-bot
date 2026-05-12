#!/usr/bin/env python3
"""UIA 消息发送者检查 v7 — PrintWindow 截图 + 像素分析."""

import argparse
import contextlib
import ctypes
import ctypes.wintypes
import sys
import threading
import time

sys.stdout.reconfigure(encoding="utf-8")

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    with contextlib.suppress(Exception):
        ctypes.windll.user32.SetProcessDPIAware()

import uiautomation as auto  # noqa: E402


def ensure_uia():
    tid = threading.get_ident()
    if not hasattr(ensure_uia, "_inits"):
        ensure_uia._inits = {}
    if tid not in ensure_uia._inits:
        ensure_uia._inits[tid] = auto.UIAutomationInitializerInThread()


def find_chat_window(group_name: str = ""):
    ensure_uia()
    root = auto.GetRootControl()
    class_names = ["mmui::ChatSingleWindow", "mmui::MainWindow", "WeChatMainWndForPC"]
    main_titles = {"微信", "WeChat", "Weixin"}
    for cls in class_names:
        for ctrl in root.GetChildren():
            try:
                if ctrl.ClassName != cls or not ctrl.Exists(0):
                    continue
                title = ctrl.Name
                if not title or title in main_titles:
                    continue
                if group_name and group_name not in title:
                    continue
                return ctrl, title
            except Exception:
                continue
    return None, ""


def capture_window_to_bmp(hwnd):
    """用 PrintWindow 截取窗口内容, 返回 (width, height, pixel_data).
    pixel_data[y][x] = (r, g, b)
    """
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    # 设置 ctypes 类型
    for fn, args, res in [
        (user32.GetDC, [ctypes.c_void_p], ctypes.c_void_p),
        (user32.ReleaseDC, [ctypes.c_void_p, ctypes.c_void_p], ctypes.c_int),
        (gdi32.CreateCompatibleDC, [ctypes.c_void_p], ctypes.c_void_p),
        (gdi32.CreateCompatibleBitmap, [ctypes.c_void_p, ctypes.c_int, ctypes.c_int], ctypes.c_void_p),
        (gdi32.SelectObject, [ctypes.c_void_p, ctypes.c_void_p], ctypes.c_void_p),
        (gdi32.DeleteObject, [ctypes.c_void_p], ctypes.c_int),
        (gdi32.DeleteDC, [ctypes.c_void_p], ctypes.c_int),
        (
            gdi32.GetDIBits,
            [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint],
            ctypes.c_int,
        ),
        (user32.PrintWindow, [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint], ctypes.c_int),
    ]:
        fn.argtypes = args
        fn.restype = res

    # 获取窗口大小
    rect = ctypes.wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    w = rect.right - rect.left
    h = rect.bottom - rect.top

    hdc_win = user32.GetDC(hwnd)
    hdc_mem = gdi32.CreateCompatibleDC(hdc_win)
    hbmp = gdi32.CreateCompatibleBitmap(hdc_win, w, h)
    gdi32.SelectObject(hdc_mem, hbmp)

    # PrintWindow with PW_RENDERFULLCONTENT (flag=2)
    result = user32.PrintWindow(hwnd, hdc_mem, 2)
    if not result:
        # fallback: PW_CLIENTONLY (flag=1) 或 default (flag=0)
        user32.PrintWindow(hwnd, hdc_mem, 0)

    # 读取位图数据
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
            ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
            ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
            ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
            ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
            ("biClrImportant", ctypes.c_uint32),
        ]

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = w
    bmi.biHeight = -h  # top-down
    bmi.biPlanes = 1
    bmi.biBitCount = 32

    buf = (ctypes.c_uint8 * (w * h * 4))()
    gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)

    gdi32.DeleteObject(hbmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(hwnd, hdc_win)

    return w, h, buf


def get_pixel_from_buf(buf, w, x, y):
    offset = (y * w + x) * 4
    return (buf[offset + 2], buf[offset + 1], buf[offset])  # BGR → RGB


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group", default="")
    parser.add_argument("--count", type=int, default=5)
    args = parser.parse_args()

    window, title = find_chat_window(args.group)
    if not window:
        print("[!] 未找到群聊窗口")
        return

    try:
        window.SetForeground()
        time.sleep(0.3)
    except Exception:
        pass

    print(f"目标窗口: {title}")
    hwnd = window.NativeWindowHandle
    print(f"HWND: {hwnd}")

    win_rect = window.BoundingRectangle
    print(f"窗口 Rect: ({win_rect.left},{win_rect.top},{win_rect.right},{win_rect.bottom})")

    # PrintWindow 截图
    w, h, buf = capture_window_to_bmp(hwnd)
    print(f"截图大小: {w}x{h}")

    # 验证: 取中心像素
    cx, cy = w // 2, h // 2
    center_px = get_pixel_from_buf(buf, w, cx, cy)
    print(f"中心像素 ({cx},{cy}): {center_px}")

    msg_list = window.ListControl(Name="消息")
    if not msg_list.Exists(0.5):
        msg_list = window.ListControl()
    if not msg_list.Exists(0):
        print("[!] 未找到消息列表")
        return

    list_rect = msg_list.BoundingRectangle
    # 转换为窗口内坐标 (UIA 给的是屏幕坐标, 需减去窗口左上角)
    ox, oy = win_rect.left, win_rect.top
    list_lx = list_rect.left - ox
    list_rx = list_rect.right - ox
    list_ty = list_rect.top - oy
    list_by = list_rect.bottom - oy
    mid_x = (list_lx + list_rx) // 2
    print(f"列表窗口内坐标: ({list_lx},{list_ty},{list_rx},{list_by}) mid_x={mid_x}")

    items = msg_list.GetChildren()
    print(f"\n共 {len(items)} 项, 看最后 {args.count} 项\n")

    for item in items[-args.count:]:
        if item.ControlTypeName != "ListItemControl" or not item.AutomationId:
            continue
        r = item.BoundingRectangle
        if r.bottom <= list_rect.top or r.top >= list_rect.bottom:
            continue

        name = item.Name
        # 转为窗口内坐标
        iy = ((r.top + r.bottom) // 2) - oy

        print(f"{'='*60}")
        print(f"Name='{name[:60]}'")
        print(f"  Rect(screen)=({r.left},{r.top},{r.right},{r.bottom})  y_local={iy}")

        # 采样: 从列表左到右, 每 30px
        samples = []
        for x in range(list_lx + 10, list_rx - 10, 30):
            if 0 <= x < w and 0 <= iy < h:
                px = get_pixel_from_buf(buf, w, x, iy)
                samples.append((x, px))

        # 打印非背景色像素
        bg = range(230, 250)
        non_bg = [(x, px) for x, px in samples if not (px[0] in bg and px[1] in bg and px[2] in bg)]
        left_nb = [(x, px) for x, px in non_bg if x < mid_x]
        right_nb = [(x, px) for x, px in non_bg if x >= mid_x]

        print(f"  Samples={len(samples)} NonBG={len(non_bg)} Left={len(left_nb)} Right={len(right_nb)}")
        for x, px in non_bg[:6]:
            side = "L" if x < mid_x else "R"
            print(f"    x={x:4d} [{side}] {px}")

        if len(right_nb) > len(left_nb) and len(right_nb) > 2:
            print("  >>> RIGHT → 自己发的")
        elif len(left_nb) > len(right_nb) and len(left_nb) > 2:
            print("  >>> LEFT  → 他人发的")
        else:
            print("  >>> UNCLEAR")
        print()


if __name__ == "__main__":
    main()
