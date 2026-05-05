"""群聊总结核心算法 - 格式化、排行榜、LLM 调用、Markdown 渲染"""
from __future__ import annotations

import html
import logging
import os
import re
import shutil
import time
from string import Template

import httpx

logger = logging.getLogger(__name__)


HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: #f5f5f5;
            padding: 40px;
            min-height: 100vh;
        }
        .container {
            max-width: 900px;
            margin: 0 auto;
            background: white;
            border-radius: 12px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
            overflow: hidden;
        }
        .header {
            background: #1a1a2e;
            color: white;
            padding: 36px 48px;
        }
        .header h1 { font-size: 32px; font-weight: 600; margin-bottom: 14px; }
        .header .meta { font-size: 20px; opacity: 0.8; }
        .header .meta span { margin-right: 32px; }
        .content { padding: 20px 24px; color: #333; line-height: 1.8; font-size: 22px; }
        .content h1 { display: none; }
        .content h2 {
            color: #1a1a2e;
            font-size: 26px;
            font-weight: 600;
            margin: 0;
            padding: 16px 20px;
            background: #f8f9fa;
            border-bottom: 1px solid #eee;
        }
        .content h3 {
            color: #1a1a2e;
            font-size: 24px;
            font-weight: 600;
            margin: 0;
            padding: 14px 20px;
            background: #f0f0f0;
            border-bottom: 1px solid #e0e0e0;
        }
        .content .section-card {
            background: white;
            border: 1px solid #e0e0e0;
            border-radius: 12px;
            margin: 12px 0;
            overflow: hidden;
        }
        .content .section-card > p,
        .content .section-card > ul,
        .content .section-card > ol {
            padding: 16px 20px 16px 48px;
            margin: 0;
        }
        .content .topic-card {
            background: #fafafa;
            border: 1px solid #e8e8e8;
            border-radius: 6px;
            margin: 16px 20px;
            overflow: hidden;
        }
        .content .topic-card ul {
            margin: 0;
            padding: 14px 20px;
            list-style: none;
        }
        .content .topic-card li {
            margin: 10px 0;
            padding-left: 0;
        }
        .content p { margin: 16px 0; text-align: justify; }
        .content ul, .content ol { margin: 16px 0 16px 32px; }
        .content li { margin: 10px 0; }
        .content strong { color: #1a1a2e; font-weight: 600; }
        .content hr { display: none; }
        .footer {
            background: #fafafa;
            padding: 20px 48px;
            text-align: right;
            color: #999;
            font-size: 16px;
            border-top: 1px solid #eee;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>$title</h1>
            <div class="meta">
                <span>$date</span>
                <span>$msg_count 条消息</span>
            </div>
        </div>
        <div class="content">
            $content
        </div>
        <div class="footer">
            $gen_time
        </div>
    </div>
</body>
</html>"""


def format_messages_for_llm(messages: list[dict]) -> tuple[str, int, dict[str, tuple[int, int]]]:
    """将消息格式化为 LLM 可读的文本，并统计发送者消息数量和字数"""
    if not messages:
        return "（无消息记录）", 0, {}

    lines = []
    sender_stats: dict[str, tuple[int, int]] = {}

    _type_labels = {3: '[图片]', 34: '[语音]', 42: '[名片]', 43: '[视频]', 47: '[动画表情]', 48: '[位置]', 49: '[链接]'}

    for msg in messages:
        if msg.get("is_self"):
            continue
        time_str = msg.get("time", "")[11:19]
        sender = msg.get("sender_name") or "未知"
        msg_type = msg.get("msg_type", 1)
        content = msg.get("content", "")
        display = _type_labels.get(msg_type, content) if msg_type != 1 else content
        lines.append(f"[{time_str}] {sender}: {display}")

        count, chars = sender_stats.get(sender, (0, 0))
        text_len = len(content) if msg_type == 1 else len(display)
        sender_stats[sender] = (count + 1, chars + text_len)

    return "\n".join(lines), len(lines), sender_stats


def generate_ranking(sender_stats: dict[str, tuple[int, int]], top_n: int = 10) -> str:
    """生成消息排行榜 Markdown"""
    if not sender_stats:
        return ""

    sorted_senders = sorted(sender_stats.items(), key=lambda x: x[1][0], reverse=True)[:top_n]
    rank_icons = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    total_chars = sum(chars for _, chars in sender_stats.values())

    lines = ["## 发言排行榜", ""]
    for i, (sender, (count, chars)) in enumerate(sorted_senders):
        icon = rank_icons[i] if i < len(rank_icons) else f"#{i + 1}"
        lines.append(f"- {icon} **{sender}** - {count} 条消息 / {chars} 字")

    lines.append("")
    lines.append(f"**总计**: {sum(c for c, _ in sender_stats.values())} 条消息 / {total_chars} 字")
    return "\n".join(lines)


def generate_ranking_text(sender_stats: dict[str, tuple[int, int]], top_n: int = 10) -> str:
    """生成消息排行榜纯文本"""
    if not sender_stats:
        return ""

    sorted_senders = sorted(sender_stats.items(), key=lambda x: x[1][0], reverse=True)[:top_n]
    rank_icons = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    total_chars = sum(chars for _, chars in sender_stats.values())

    lines = ["发言排行榜:"]
    for i, (sender, (count, chars)) in enumerate(sorted_senders):
        icon = rank_icons[i] if i < len(rank_icons) else f"#{i + 1}"
        lines.append(f"{icon} {sender} - {count} 条 / {chars} 字")

    lines.append(f"总计: {sum(c for c, _ in sender_stats.values())} 条 / {total_chars} 字")
    return "\n".join(lines)


def extract_overview(summary_md: str) -> str:
    """从总结 Markdown 中提取概览段落"""
    lines = summary_md.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^##\s*概览\s*$", line.strip()):
            start = i + 1
            break
    if start is None:
        return ""

    end = len(lines)
    for j in range(start, len(lines)):
        if re.match(r"^##\s+\S", lines[j].strip()):
            end = j
            break

    return "\n".join(lines[start:end]).strip()


def _strip_markdown(text: str) -> str:
    s = text or ""
    s = re.sub(r"^\s*#+\s*", "", s, flags=re.MULTILINE)
    s = re.sub(r"\*\*(.*?)\*\*", r"\1", s)
    s = re.sub(r"\*(.*?)\*", r"\1", s)
    s = re.sub(r"`(.*?)`", r"\1", s)
    s = re.sub(r"^[-*•]\s*", "", s, flags=re.MULTILINE)
    s = re.sub(r"^\s*\d+\.\s*", "", s, flags=re.MULTILINE)
    return s.strip()


def build_summary_text(overview: str, ranking: str, group_name: str) -> str:
    """组合发送用的文本消息：概览 + 排行榜"""
    header = f"【{group_name}】群聊总结" if group_name else "群聊总结"
    overview_text = _strip_markdown(overview) if overview else ""
    if not overview_text:
        overview_text = "（无）"
    parts = [header, "", "概览：", overview_text, ""]
    ranking_text = _strip_markdown(ranking) if ranking else ""
    if ranking_text:
        parts.append(ranking_text)
    return "\n".join(parts).strip()


def summarize_with_llm(
    messages_text: str,
    group_name: str,
    date_str: str,
    api_url: str,
    api_key: str,
    model: str = "gpt-4o-mini",
    client: httpx.Client | None = None,
) -> str:
    """使用 LLM 生成聊天记录总结"""
    system_prompt = """你是一个专业的群聊记录分析助手。请分析提供的聊天记录，生成结构化的 Markdown 格式总结。

总结应包含以下部分：

## 概览
简要描述今日群聊的整体氛围和活跃度（1-2句话）

## 话题分析
识别出所有讨论话题，对于每个话题提供：
- **主题** - 话题的简短标题
- **时间**: 起止时间（如 09:30-10:15）
- **参与者**: 主要参与讨论的成员
- **内容**: 详细描述讨论过程，包括各方观点、提出的问题、给出的解答或建议、达成的共识等（3-5句话）

格式示例：
### 话题一：[主题名称]
- **时间**: 09:30 - 10:15
- **参与者**: 张三、李四、王五
- **内容**: 张三提出了xxx问题，李四建议使用xxx方案，王五补充了xxx细节。经过讨论，大家认为xxx是最佳选择，并计划xxx。

## 重要信息
提取值得关注的信息、决定或结论（如无则写"无"）

注意：
- 使用中文输出
- 话题按时间顺序排列
- 合并相似或连续的讨论
- 内容描述要具体，体现讨论的来龙去脉
- 忽略表情包、无意义的水群内容
- 如果聊天内容较少或无实质内容，如实说明
- 不要生成活跃成员/发言排行相关内容，这部分由程序自动统计"""

    user_prompt = f"""请总结以下群聊记录：

群聊: {group_name}
日期: {date_str}

聊天记录:
---
{messages_text}
---

请生成 Markdown 格式的总结。"""

    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "max_tokens": 2000,
        "temperature": 0.3,
    }

    if client:
        response = client.post("/chat/completions", json=payload, timeout=60)
    else:
        response = httpx.post(f"{api_url}/chat/completions", headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"].strip()


def _escape_and_bold(text: str) -> str:
    """先转义 HTML，再恢复 **bold** 为 <strong>"""
    escaped = html.escape(text)
    return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped)


def markdown_to_html(md_text: str) -> str:
    """将 Markdown 转换为 HTML，使用卡片形式"""
    lines = md_text.split('\n')
    html_lines = []
    in_list = False
    in_card = False

    for line in lines:
        stripped = line.strip()

        if stripped.startswith('### '):
            if in_list:
                html_lines.append('</ul>')
                in_list = False
            if in_card:
                html_lines.append('</div>')
            html_lines.append('<div class="section-card">')
            html_lines.append(f'<h3>{_escape_and_bold(stripped[4:])}</h3>')
            in_card = True
        elif stripped.startswith('## '):
            if in_list:
                html_lines.append('</ul>')
                in_list = False
            if in_card:
                html_lines.append('</div>')
            html_lines.append('<div class="section-card">')
            html_lines.append(f'<h2>{_escape_and_bold(stripped[3:])}</h2>')
            in_card = True
        elif stripped.startswith('# '):
            if in_list:
                html_lines.append('</ul>')
                in_list = False
            if in_card:
                html_lines.append('</div>')
                in_card = False
            html_lines.append(f'<h1>{_escape_and_bold(stripped[2:])}</h1>')
        elif stripped.startswith('- '):
            if not in_list:
                html_lines.append('<ul>')
                in_list = True
            html_lines.append(f'<li>{_escape_and_bold(stripped[2:])}</li>')
        elif not stripped:
            if in_list:
                html_lines.append('</ul>')
                in_list = False
        else:
            if in_list:
                html_lines.append('</ul>')
                in_list = False
            html_lines.append(f'<p>{_escape_and_bold(stripped)}</p>')

    if in_list:
        html_lines.append('</ul>')
    if in_card:
        html_lines.append('</div>')

    return '\n'.join(html_lines)


def render_to_image(
    summary: str,
    date_str: str,
    msg_count: int,
    gen_time: str,
    output_path: str,
    group_name: str = "",
) -> bool:
    """使用 html2image 将总结渲染为图片，自动裁剪空白"""
    try:
        from html2image import Html2Image
    except ImportError:
        logger.error("缺少依赖 html2image: uv add html2image")
        return False

    try:
        from PIL import Image
    except ImportError:
        logger.error("缺少依赖 Pillow: uv add Pillow")
        return False

    html_content = markdown_to_html(summary)

    base_height = 500
    content_height = len(summary) * 1.6 + summary.count('\n') * 50
    estimated_height = int(base_height + content_height)
    render_height = max(estimated_height, 1500) + 800

    template = Template(HTML_TEMPLATE)
    safe_group = html.escape(group_name) if group_name else ""
    title = f"【{safe_group}】群聊总结" if safe_group else "群聊总结"
    full_html = template.safe_substitute(
        title=title,
        date=date_str,
        msg_count=msg_count,
        gen_time=gen_time,
        content=html_content,
    )

    try:
        browser_path = None
        edge_paths = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
        if not shutil.which("chrome") and not shutil.which("google-chrome"):
            for edge_path in edge_paths:
                if os.path.exists(edge_path):
                    browser_path = edge_path
                    logger.info(f"使用 Edge 浏览器: {edge_path}")
                    break

        output_dir = os.path.dirname(output_path) or os.getcwd()
        output_dir = os.path.abspath(output_dir)
        os.makedirs(output_dir, exist_ok=True)

        output_name = os.path.basename(output_path)
        if not output_name.endswith('.png'):
            output_name += '.png'

        if browser_path:
            hti = Html2Image(size=(900, render_height), browser_executable=browser_path, output_path=output_dir)
        else:
            hti = Html2Image(size=(900, render_height), output_path=output_dir)

        temp_name = f"_temp_{output_name}"
        temp_path = os.path.join(output_dir, temp_name)
        final_path = os.path.join(output_dir, output_name)

        if os.path.exists(temp_path):
            os.remove(temp_path)

        html_file_path = os.path.join(output_dir, f"_temp_{output_name}.html")
        with open(html_file_path, "w", encoding="utf-8") as f:
            f.write(full_html)

        file_url = f"file:///{html_file_path.replace(os.sep, '/')}"
        hti.screenshot(url=file_url, save_as=temp_name)

        for _ in range(10):
            if os.path.exists(temp_path):
                break
            time.sleep(0.5)

        if not os.path.exists(temp_path):
            logger.error(f"渲染图片失败: 临时文件未生成 {temp_path}")
            return False

        img = Image.open(temp_path)
        pixels = img.load()
        width, height = img.size

        bottom = height
        center_x = width // 2
        bg_color = (245, 245, 245)

        for y in range(height - 1, 100, -1):
            r, g, b = pixels[center_x, y][:3]
            if not (r == bg_color[0] and g == bg_color[1] and b == bg_color[2]):
                bottom = y + 40
                break

        bottom = min(bottom, height)
        if bottom < height:
            img = img.crop((0, 0, width, bottom))

        img.save(final_path)
        img.close()

        if os.path.exists(temp_path) and temp_path != final_path:
            os.remove(temp_path)
        if os.path.exists(html_file_path):
            os.remove(html_file_path)

        return True
    except Exception as e:
        logger.error(f"渲染图片失败: {e}", exc_info=True)
        return False
