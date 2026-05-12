"""
OCR 工具 - 使用 macOS Vision 框架,支持 CGImage 直接识别
"""

import logging

import Vision

from common.config import config
from gateway.utils.screenshot import capture_window, crop_chat_region

logger = logging.getLogger(__name__)


def ocr_image(cg_image) -> list:
    """
    使用 macOS Vision 框架对 CGImage 进行 OCR(无磁盘 I/O)

    Args:
        cg_image: Quartz CGImageRef

    Returns:
        list: [{'text': str, 'confidence': float, 'x': float, 'y': float, 'width': float}, ...]
              x, y, width 为 0-1 的比例值
              x < 0.5 表示左侧(他人消息),x >= 0.5 表示右侧(自己消息)
    """
    handler = Vision.VNImageRequestHandler.alloc().initWithCGImage_options_(cg_image, None)

    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    request.setRecognitionLanguages_(config.OCR_LANGUAGES)
    request.setAutomaticallyDetectsLanguage_(True)
    request.setMinimumTextHeight_(config.OCR_MIN_TEXT_HEIGHT)
    request.setCustomWords_(config.OCR_CUSTOM_WORDS)

    success, error = handler.performRequests_error_([request], None)
    if not success:
        logger.error(f"OCR 错误: {error}")
        return []

    results = []
    for obs in request.results():
        text = obs.topCandidates_(1)[0].string()
        conf = obs.confidence()
        bbox = obs.boundingBox()
        results.append({
            'text': text,
            'confidence': conf,
            'x': bbox.origin.x,
            'y': bbox.origin.y,
            'width': bbox.size.width,
        })

    # 按 y 坐标排序(从上到下)
    results.sort(key=lambda r: -r['y'])
    return results


def get_others_messages(ocr_results: list) -> list:
    """
    从 OCR 结果中提取他人消息(左侧消息),合并同一行碎片和多行消息.

    处理流程:
    1. 筛选左侧文本
    2. 合并同一行的 OCR 碎片(y 差值 < OCR_SAME_LINE_Y_GAP,按 x 排序拼接)
    3. 标记昵称行(y 差值 < OCR_NICKNAME_Y_GAP 且 x 差值 > OCR_NICKNAME_X_DIFF)
    4. 按 y 间距分组合并多行消息(间距 > OCR_MESSAGE_GAP 或遇到昵称则分隔)

    Args:
        ocr_results: ocr_image 返回的结果列表(已按 y 降序排列)

    Returns:
        list: 消息文本列表 ['消息1', '消息2', ...]
    """
    # 1. 筛选左侧文本
    left_items = [r for r in ocr_results if r['x'] < config.OCR_LEFT_THRESHOLD]

    # 2. 合并同一行的 OCR 碎片(y 几乎相同的多个识别结果)
    lines = []
    for item in left_items:
        if lines and abs(lines[-1][0]['y'] - item['y']) < config.OCR_SAME_LINE_Y_GAP:
            lines[-1].append(item)
        else:
            lines.append([item])

    merged_lines = []
    for group in lines:
        group.sort(key=lambda r: r['x'])
        text = ''.join(r['text'] for r in group)
        merged_lines.append({
            'text': text,
            'y': group[0]['y'],
            'x': min(r['x'] for r in group),
            'confidence': max(r['confidence'] for r in group),
        })

    # 3. 标记昵称行
    nickname_indices = set()
    for i in range(len(merged_lines) - 1):
        y_gap = merged_lines[i]['y'] - merged_lines[i + 1]['y']
        x_diff = merged_lines[i + 1]['x'] - merged_lines[i]['x']
        if 0 < y_gap < config.OCR_NICKNAME_Y_GAP and x_diff > config.OCR_NICKNAME_X_DIFF:
            nickname_indices.add(i)

    # 4. 按 y 间距分组合并多行消息
    messages = []
    current_lines = []
    prev_y = None

    for i, line in enumerate(merged_lines):
        if i in nickname_indices:
            if current_lines:
                messages.append('\n'.join(current_lines))
                current_lines = []
            prev_y = line['y']
            continue

        if line['confidence'] < config.OCR_CONFIDENCE_THRESHOLD:
            prev_y = line['y']
            continue

        text = line['text'].strip()
        if len(text) < config.OCR_MIN_TEXT_LENGTH:
            prev_y = line['y']
            continue

        # y 间距大于阈值或遇到昵称后的第一行 → 新消息
        if prev_y is not None and prev_y - line['y'] > config.OCR_MESSAGE_GAP and current_lines:
            messages.append('\n'.join(current_lines))
            current_lines = []

        current_lines.append(text)
        prev_y = line['y']

    if current_lines:
        messages.append('\n'.join(current_lines))

    return messages


def screenshot_and_ocr(window_id: int) -> list:
    """按窗口 ID 截图并 OCR 识别(纯内存,无需激活窗口)

    Args:
        window_id: Quartz 窗口 ID (kCGWindowNumber)

    Returns:
        OCR 结果列表
    """
    image = capture_window(window_id)
    if image is None:
        return []

    cropped = crop_chat_region(image)
    if cropped is None:
        return []

    return ocr_image(cropped)
