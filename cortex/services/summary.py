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
            font-family: "Avenir Next", "PingFang SC", "Microsoft YaHei", sans-serif;
            background: #f5f5f5;
            padding: 32px;
            min-height: 100vh;
            color: #232323;
        }
        .sheet {
            width: 836px;
            margin: 0 auto;
            background:
                linear-gradient(rgba(38, 30, 18, 0.025) 1px, transparent 1px),
                linear-gradient(90deg, rgba(38, 30, 18, 0.025) 1px, transparent 1px),
                #f4eddf;
            background-size: 18px 18px;
            border: 2px solid #1d1d1f;
            box-shadow: 8px 8px 0 #1d1d1f;
            padding: 28px;
        }
        .header {
            margin-bottom: 20px;
        }
        .title-card,
        .report-panel {
            background: rgba(255, 251, 239, 0.82);
            border: 2px solid #1d1d1f;
            box-shadow: 4px 4px 0 rgba(29, 29, 31, 0.18);
        }
        .title-card {
            padding: 24px 28px 20px;
            position: relative;
        }
        .title-card::after {
            content: "";
            position: absolute;
            right: 22px;
            top: 22px;
            width: 72px;
            height: 72px;
            border: 3px solid #c54539;
            border-radius: 50%;
            opacity: 0.16;
            transform: rotate(-12deg);
        }
        .label {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            color: #b43d35;
            font-size: 15px;
            font-weight: 800;
            margin-bottom: 14px;
        }
        .label::before {
            content: "";
            width: 28px;
            height: 5px;
            background: #c54539;
            display: inline-block;
        }
        .title-card h1 {
            font-size: 38px;
            line-height: 1.12;
            font-weight: 850;
            letter-spacing: 0;
            margin-bottom: 12px;
        }
        .subtitle {
            font-size: 16px;
            line-height: 1.55;
            color: #5f574c;
            max-width: 620px;
        }
        .title-meta {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin-top: 18px;
            padding-top: 16px;
            border-top: 2px solid rgba(29, 29, 31, 0.16);
        }
        .meta-chip {
            display: inline-flex;
            align-items: center;
            min-height: 30px;
            padding: 5px 10px;
            background: #fff4d7;
            border: 1px solid #1d1d1f;
            color: #322d26;
            font-size: 14px;
            font-weight: 750;
        }
        .meta-chip.strong {
            background: #1d1d1f;
            color: #f7f0df;
        }
        .meta-chip.warn {
            color: #a33831;
        }
        .content {
            display: grid;
            grid-template-columns: 1fr;
            gap: 20px;
            align-items: start;
        }
        .report-panel { overflow: hidden; }
        .report-panel { grid-column: 1; }
        .panel-title {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 14px 16px;
            background: #f6e9c8;
            border-bottom: 2px solid #1d1d1f;
            font-size: 21px;
            line-height: 1.25;
            font-weight: 850;
        }
        .panel-tag {
            display: inline-block;
            padding: 3px 8px;
            background: #c54539;
            color: #fff3dc;
            border: 1px solid #1d1d1f;
            transform: rotate(-3deg);
            font-size: 12px;
            line-height: 1;
            font-weight: 850;
        }
        .panel-body {
            padding: 18px;
            background: rgba(255, 252, 242, 0.78);
        }
        .panel-body p {
            font-size: 19px;
            line-height: 1.75;
            text-align: justify;
        }
        .panel-body > ul {
            padding-left: 22px;
            font-size: 18px;
            line-height: 1.65;
        }
        .panel-body > ul li { margin: 8px 0; }
        .topic-card {
            position: relative;
            padding: 14px 16px 14px 22px;
            margin-bottom: 16px;
            background: rgba(255, 251, 239, 0.86);
            border-left: 7px solid #3d8f81;
            border-top: 1px solid rgba(29, 29, 31, 0.18);
            border-right: 1px solid rgba(29, 29, 31, 0.18);
            border-bottom: 1px solid rgba(29, 29, 31, 0.18);
        }
        .topic-card:nth-child(2n) { border-left-color: #c54539; }
        .topic-card:nth-child(3n) { border-left-color: #bd8c2c; }
        .topic-card h3 {
            font-size: 20px;
            line-height: 1.35;
            margin-bottom: 10px;
            font-weight: 850;
        }
        .topic-card ul { list-style: none; }
        .topic-card li {
            display: grid;
            grid-template-columns: 76px 1fr;
            gap: 12px;
            margin: 8px 0;
            font-size: 17px;
            line-height: 1.55;
        }
        .topic-card strong {
            color: #756143;
            white-space: nowrap;
            font-weight: 850;
        }
        .section-ranking .panel-body > ul { list-style: none; padding-left: 0; }
        .section-ranking .panel-body > ul li {
            border-bottom: 1px dashed rgba(29, 29, 31, 0.24);
            padding: 10px 0;
            font-size: 18px;
            line-height: 1.55;
        }
        .section-ranking .panel-body > ul li:last-child { border-bottom: 0; }
        .section-ranking strong { color: #1d1d1f; }
        .emoji-img {
            display: inline-block;
            width: 1.18em;
            height: 1.18em;
            margin: 0 0.02em;
            vertical-align: -0.2em;
        }
    </style>
</head>
<body>
    <div class="sheet">
        <div class="header">
            <div class="title-card">
                <div class="label">群聊总结简报</div>
                <h1>$title</h1>
                <div class="subtitle">按群友可读性整理当日重点话题，仅作娱乐总结。</div>
                <div class="title-meta">
                    <span class="meta-chip strong">$date</span>
                    <span class="meta-chip warn">$msg_count 条有效消息</span>
                    <span class="meta-chip">$gen_time 生成</span>
                </div>
            </div>
        </div>
        <div class="content">
            $content
        </div>
    </div>
</body>
</html>"""


EMOJI_IMAGE_MAP = {
    "🪼": "https://cdn.jsdelivr.net/npm/openmoji@15.1.0/color/svg/1FABC.svg",
}


def _replace_emoji_images(escaped_text: str) -> str:
    for emoji, url in EMOJI_IMAGE_MAP.items():
        escaped_text = escaped_text.replace(
            emoji,
            f'<img class="emoji-img" alt="{emoji}" src="{url}">',
        )
    return escaped_text


def _summary_inline(value: object, fallback: str = "") -> str:
    text = str(value or "")
    text = re.sub(r"[\r\n\u2028\u2029]+", " ", text)
    text = re.sub(r"[ \t\f\v]+", " ", text).strip()
    return text or fallback


def _summary_member_label(sender_id: str, sender_name: str, members_by_id: dict[str, dict]) -> str:
    member = members_by_id.get(sender_id) or {}
    group_name = _summary_inline(member.get("display_name") or sender_name)
    nick_name = _summary_inline(member.get("nick_name"))
    if group_name and nick_name and group_name != nick_name:
        return f"{group_name} ({nick_name})"
    return _summary_inline(group_name or nick_name or member.get("alias") or sender_name, "未知")


def format_messages_for_llm(
    messages: list[dict],
    members: list[dict] | None = None,
) -> tuple[str, int, dict[str, tuple[int, int, str]]]:
    """将消息格式化为 LLM 可读的文本,并统计发送者消息数量和字数"""
    if not messages:
        return "(无消息记录)", 0, {}

    lines = []
    sender_stats: dict[str, tuple[int, int, str]] = {}
    members_by_id = {
        str(member.get("username") or ""): member
        for member in (members or [])
        if member.get("username")
    }

    _type_labels = {3: '[图片]', 34: '[语音]', 42: '[名片]', 43: '[视频]', 47: '[动画表情]', 48: '[位置]', 49: '[链接]'}

    for msg in messages:
        if msg.get("is_self"):
            continue
        time_str = msg.get("time", "")[11:19]
        sender = _summary_inline(msg.get("sender_name"), "未知")
        sender_id = _summary_inline(msg.get("sender")) or sender
        stat_sender = sender_id
        stat_label = _summary_member_label(sender_id, sender, members_by_id)
        msg_type = msg.get("msg_type", 1)
        content = msg.get("content", "")
        display = _type_labels.get(msg_type, content) if msg_type != 1 else content
        display = _summary_inline(display)
        lines.append(f"[{time_str}] {sender}: {display}")

        count, chars, _ = sender_stats.get(stat_sender, (0, 0, stat_label))
        text_len = len(content) if msg_type == 1 else len(display)
        sender_stats[stat_sender] = (count + 1, chars + text_len, stat_label)

    return "\n".join(lines), len(lines), sender_stats


def generate_ranking(sender_stats: dict[str, tuple[int, int, str]], top_n: int = 10) -> str:
    """生成消息排行榜 Markdown"""
    if not sender_stats:
        return ""

    sorted_senders = sorted(sender_stats.items(), key=lambda x: x[1][0], reverse=True)[:top_n]
    rank_icons = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    total_chars = sum(chars for _, chars, _ in sender_stats.values())

    lines = ["## 发言排行榜", ""]
    for i, (_sender_id, (count, chars, sender)) in enumerate(sorted_senders):
        icon = rank_icons[i] if i < len(rank_icons) else f"#{i + 1}"
        sender = _summary_inline(sender, "未知")
        lines.append(f"- {icon} **{sender}** - {count} 条消息 / {chars} 字")

    lines.append("")
    lines.append(f"**总计**: {sum(c for c, _, _ in sender_stats.values())} 条消息 / {total_chars} 字")
    return "\n".join(lines)


def generate_ranking_text(sender_stats: dict[str, tuple[int, int, str]], top_n: int = 10) -> str:
    """生成消息排行榜纯文本"""
    if not sender_stats:
        return ""

    sorted_senders = sorted(sender_stats.items(), key=lambda x: x[1][0], reverse=True)[:top_n]
    rank_icons = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    total_chars = sum(chars for _, chars, _ in sender_stats.values())

    lines = ["发言排行榜:"]
    for i, (_sender_id, (count, chars, sender)) in enumerate(sorted_senders):
        icon = rank_icons[i] if i < len(rank_icons) else f"#{i + 1}"
        sender = _summary_inline(sender, "未知")
        lines.append(f"{icon} {sender} - {count} 条 / {chars} 字")

    lines.append(f"总计: {sum(c for c, _, _ in sender_stats.values())} 条 / {total_chars} 字")
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
    """组合发送用的文本消息:概览 + 排行榜"""
    group_name = _summary_inline(group_name)
    header = f"【{group_name}】群聊总结" if group_name else "群聊总结"
    overview_text = _strip_markdown(overview) if overview else ""
    if not overview_text:
        overview_text = "(无)"
    parts = [header, "", "概览:", overview_text, ""]
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
    system_prompt = """你是一个专业的群聊记录分析助手.请分析提供的聊天记录,生成结构化的 Markdown 格式总结.

总结应包含以下部分:

## 概览
简要描述今日群聊的整体氛围和活跃度(1-2句话)

## 话题分析
识别出所有讨论话题,对于每个话题提供:
- **主题** - 话题的简短标题
- **时间**: 起止时间(如 09:30-10:15)
- **参与者**: 主要参与讨论的成员
- **内容**: 详细描述讨论过程,包括各方观点、提出的问题、给出的解答或建议、达成的共识等(3-5句话)

格式示例:
### 话题一:[主题名称]
- **时间**: 09:30 - 10:15
- **参与者**: 张三、李四、王五
- **内容**: 张三提出了xxx问题,李四建议使用xxx方案,王五补充了xxx细节.经过讨论,大家认为xxx是最佳选择,并计划xxx.

## 重要信息
提取值得关注的信息、决定或结论(如无则写"无")

注意:
- 使用中文输出
- 聊天记录中冒号前的人才是该条消息的发言人.
- 不要把消息内容里提到的人名当成发言人.
- 话题按时间顺序排列
- 合并相似或连续的讨论
- 内容描述要具体,体现讨论的来龙去脉
- 忽略表情包、无意义的水群内容
- 如果聊天内容较少或无实质内容,如实说明
- 不要生成活跃成员/发言排行相关内容,这部分由程序自动统计"""

    user_prompt = f"""请总结以下群聊记录:

群聊: {group_name}
日期: {date_str}

聊天记录格式: [时间] 发言人: 内容

聊天记录:
---
{messages_text}
---

请生成 Markdown 格式的总结."""

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


def summarize_topic_with_llm(
    messages_text: str,
    group_name: str,
    topic: str,
    date_str: str,
    model: str = "gpt-4o-mini",
    client: httpx.Client | None = None,
) -> str:
    """使用 LLM 按主题生成聊天记录总结"""
    system_prompt = """你是群聊记录检索整理助手.用户会给一个主题、问题或分析目标,请只根据聊天记录生成一份围绕该目标的中文 Markdown 答案.

任务原则:
- 先判断用户真正想知道什么,再组织答案.
- 输出要直接、有用、贴合主题,不要泛泛总结整段聊天.
- 结构可以根据主题自行选择,不要求固定章节.
- 只保留和主题直接相关或明显相关的内容,忽略无关闲聊.
- 不要编造聊天记录中没有出现的信息.
- 重要判断要给出依据,可以是时间、发言人、原话摘要或上下文.
- 严格区分发言归属: 聊天记录中冒号前的人才是该条消息的发言人,不要把消息内容里提到的人名当成发言人.
- 严格区分"本人发言"和"他人提及/评价".人物画像、人物关系、最近发言类主题中,不能把别人对某人的评价当成这个人的自我表达.
- 不要推断聊天记录没有明确说明的人物关系、身份、立场或动机.如果只能看到互动或玩笑,就按互动或玩笑描述,不要升级成确定关系.
- 如果多个同名/相似昵称可能混淆,必须说明无法确认,不要合并成一个人.
- 如果信息不足,直接说明最近记录里没找到足够相关信息.
- 医疗、法律、投资等高风险内容,只整理聊天里实际出现过的说法,不额外给专业建议.

主题适配:
- 如果是人物画像,优先基于目标人物自己的发言;其他人提到目标人物的内容只能放在"他人提及/互动"一类,不能混入本人特征.
- 如果是最近发言,只总结目标人物自己发出的消息;别人回复或提到他的消息只能作为上下文.
- 如果是注意事项/怎么办,重点整理聊天里出现过的建议、风险、步骤、共识和分歧.
- 如果是信息检索,直接回答用户问题,并列出相关证据.
- 如果是事件或话题复盘,按时间和讨论脉络整理."""

    user_prompt = f"""请从以下群聊记录中提取并总结指定主题:

群聊: {group_name}
时间范围: {date_str}
主题: {topic}

聊天记录格式: [时间] 发言人: 内容

聊天记录:
---
{messages_text}
---

请只生成与主题相关的 Markdown 答案,不要输出无关内容."""

    if client is None:
        raise ValueError("主题总结需要已初始化的 LLM HTTP 客户端")

    response = client.post(
        "/chat/completions",
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "max_tokens": 1600,
            "temperature": 0.2,
        },
        timeout=60,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"].strip()


def _escape_and_bold(text: str) -> str:
    """先转义 HTML,再恢复 **bold** 为 <strong>"""
    escaped = html.escape(text)
    escaped = _replace_emoji_images(escaped)
    return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped)


def _section_meta(title: str) -> tuple[str, str]:
    if "概览" in title:
        return "overview", "CASE"
    if "话题" in title:
        return "topics", "CASE"
    if "重要" in title:
        return "important", "EVID"
    if "排行" in title:
        return "ranking", "EVID"
    return "general", "NOTE"


def markdown_to_html(md_text: str) -> str:
    """将 Markdown 转换为 HTML,使用卡片形式"""
    lines = md_text.split('\n')
    html_lines = []
    in_list = False
    in_section = False
    in_topic = False

    def close_list() -> None:
        nonlocal in_list
        if in_list:
            html_lines.append('</ul>')
            in_list = False

    def close_topic() -> None:
        nonlocal in_topic
        if in_topic:
            close_list()
            html_lines.append('</article>')
            in_topic = False

    def close_section() -> None:
        nonlocal in_section
        if in_section:
            close_topic()
            close_list()
            html_lines.append('</div></section>')
            in_section = False

    def ensure_section() -> None:
        nonlocal in_section
        if not in_section:
            html_lines.append('<section class="report-panel section-general"><div class="panel-title"><span class="panel-tag">NOTE</span>记录</div><div class="panel-body">')
            in_section = True

    for line in lines:
        stripped = line.strip()

        if stripped.startswith('### '):
            ensure_section()
            close_topic()
            html_lines.append(f'<article class="topic-card"><h3>{_escape_and_bold(stripped[4:])}</h3>')
            in_topic = True
        elif stripped.startswith('## '):
            close_section()
            title = stripped[3:]
            section_class, tag = _section_meta(title)
            html_lines.append(
                f'<section class="report-panel section-{section_class}">'
                f'<div class="panel-title"><span class="panel-tag">{tag}</span>{_escape_and_bold(title)}</div>'
                '<div class="panel-body">'
            )
            in_section = True
        elif stripped.startswith('# '):
            close_section()
            html_lines.append(f'<h1>{_escape_and_bold(stripped[2:])}</h1>')
        elif stripped.startswith('- '):
            ensure_section()
            if not in_list:
                html_lines.append('<ul>')
                in_list = True
            html_lines.append(f'<li>{_escape_and_bold(stripped[2:])}</li>')
        elif not stripped:
            close_list()
        else:
            ensure_section()
            close_list()
            html_lines.append(f'<p>{_escape_and_bold(stripped)}</p>')

    close_section()

    return '\n'.join(html_lines)


def render_to_image(
    summary: str,
    date_str: str,
    msg_count: int,
    gen_time: str,
    output_path: str,
    group_name: str = "",
    title_override: str | None = None,
) -> bool:
    """使用 html2image 将总结渲染为图片,自动裁剪空白"""
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
    title = html.escape(title_override) if title_override else (f"【{safe_group}】群聊总结" if safe_group else "群聊总结")
    title = _replace_emoji_images(title)
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
