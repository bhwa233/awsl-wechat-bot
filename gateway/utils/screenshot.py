"""
截图工具 - 使用 Quartz 按窗口 ID 直接内存截图,无需激活窗口
"""

import logging

import Quartz

from common.config import config

logger = logging.getLogger(__name__)


def capture_window(window_id: int) -> "Quartz.CGImageRef | None":
    """按窗口 ID 截取窗口图像(内存中,无需激活窗口)

    Returns:
        CGImageRef 或 None
    """
    image = Quartz.CGWindowListCreateImage(
        Quartz.CGRectNull,
        Quartz.kCGWindowListOptionIncludingWindow,
        window_id,
        Quartz.kCGWindowImageBoundsIgnoreFraming,
    )
    if image is None:
        logger.warning(f"截取窗口 {window_id} 失败")
        return None
    return image


def crop_chat_region(image, popup: bool = True) -> "Quartz.CGImageRef":
    """裁剪聊天消息区域(去掉标题栏、输入框、左侧栏)

    Args:
        image: CGImageRef
        popup: 是否为弹出聊天窗口(无左侧联系人栏)

    Returns:
        裁剪后的 CGImageRef
    """
    w = Quartz.CGImageGetWidth(image)
    h = Quartz.CGImageGetHeight(image)

    if popup:
        crop_x = 0
        crop_y = int(h * config.SCREENSHOT_TOP_RATIO)
        crop_w = w
        crop_h = int(h * config.SCREENSHOT_HEIGHT_RATIO)
    else:
        crop_x = int(w * config.SCREENSHOT_LEFT_RATIO)
        crop_y = int(h * config.SCREENSHOT_TOP_RATIO)
        crop_w = int(w * config.SCREENSHOT_WIDTH_RATIO)
        crop_h = int(h * config.SCREENSHOT_HEIGHT_RATIO)

    rect = Quartz.CGRectMake(crop_x, crop_y, crop_w, crop_h)
    return Quartz.CGImageCreateWithImageInRect(image, rect)
