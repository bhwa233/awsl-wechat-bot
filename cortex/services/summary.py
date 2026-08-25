"""群聊总结核心算法 - 格式化、排行榜、LLM 调用、Markdown 渲染"""
from __future__ import annotations

import html
import logging
import os
import re
import time
from pathlib import Path
from string import Template

import httpx

from common.config import config

logger = logging.getLogger(__name__)


HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: "Microsoft YaHei", "DengXian", "PingFang SC", sans-serif;
            background: #ededed;
            padding: 24px;
            min-height: 100vh;
            color: #191919;
        }
        .paper {
            --ink: #191919;
            --ink-soft: #576b65;
            --wechat-green: #07c160;
            --line: #e5e5e5;
            --paper: #ffffff;
            width: 852px;
            margin: 0 auto;
            background: var(--paper);
            border-top: 6px solid var(--wechat-green);
            box-shadow: 0 12px 32px rgba(0, 0, 0, 0.12);
            padding: 0 46px 42px;
            overflow: hidden;
            position: relative;
        }
        .title-layout {
            display: block;
            padding: 0;
        }
        h1 {
            max-width: 100%;
            color: var(--ink);
            font-family: "Microsoft YaHei", "DengXian", "PingFang SC", sans-serif;
            font-size: 44px;
            line-height: 1.25;
            font-weight: 700;
            letter-spacing: -0.02em;
        }
        .content { padding-top: 30px; }
        .report-section {
            margin-top: 42px;
            position: relative;
        }
        .report-section:first-child { margin-top: 0; }
        .section-heading {
            display: flex;
            align-items: center;
            gap: 12px;
            margin-bottom: 20px;
            padding: 0;
            border: 0;
        }
        .section-heading::before {
            content: "";
            width: 18px;
            height: 4px;
            flex: 0 0 auto;
            background: var(--wechat-green);
        }
        .section-heading::after {
            content: "";
            height: 1px;
            flex: 1;
            margin-left: 2px;
            background: var(--line);
        }
        .section-heading h2 {
            color: var(--ink);
            font-family: "Microsoft YaHei", "DengXian", "PingFang SC", sans-serif;
            font-size: 25px;
            line-height: 1.35;
            font-weight: 700;
        }
        .section-body p,
        .section-body li {
            font-size: 19px;
            line-height: 1.76;
        }
        .section-body p { margin: 0 0 14px; }
        .section-body ul,
        .section-body ol {
            padding-left: 24px;
            margin: 10px 0 16px;
        }
        .section-body li { margin: 8px 0; }
        .section-body > :last-child { margin-bottom: 0; }
        .section-body blockquote {
            margin: 16px 0;
            padding: 13px 18px;
            border-left: 4px solid var(--wechat-green);
            background: #f7f7f7;
            color: var(--ink);
        }
        .section-body pre {
            overflow: hidden;
            white-space: pre-wrap;
            word-break: break-word;
            margin: 16px 0;
            padding: 16px;
            background: #2c2c2c;
            color: #ffffff;
            font-size: 14px;
            line-height: 1.6;
        }
        .section-body code {
            padding: 1px 5px;
            background: rgba(0, 0, 0, 0.06);
            border-radius: 3px;
            font-family: "SFMono-Regular", Consolas, monospace;
            font-size: 0.92em;
        }
        .section-body pre code {
            padding: 0;
            background: transparent;
            border-radius: 0;
            color: inherit;
            font-size: inherit;
        }
        .section-body table {
            width: 100%;
            border-collapse: collapse;
            margin: 16px 0;
            font-size: 16px;
            line-height: 1.5;
        }
        .section-body th,
        .section-body td {
            border-bottom: 1px solid var(--line);
            padding: 9px 11px;
            vertical-align: top;
            text-align: left;
        }
        .section-body th {
            background: #f7f7f7;
            font-weight: 700;
        }
        .topics-list {
            border-top: 0;
        }
        .topic-card {
            display: grid;
            grid-template-columns: 38px minmax(0, 1fr);
            gap: 14px;
            padding: 24px 0 26px;
            border-bottom: 1px solid var(--line);
        }
        .topic-card:first-child { padding-top: 2px; }
        .topic-index {
            width: 38px;
            height: auto;
            display: block;
            padding-top: 3px;
            background: transparent;
            color: var(--wechat-green);
            border-radius: 0;
            font-family: "Microsoft YaHei", sans-serif;
            font-size: 16px;
            font-weight: 700;
        }
        .topic-card h3 {
            color: var(--ink);
            font-family: "Microsoft YaHei", "DengXian", "PingFang SC", sans-serif;
            font-size: 21px;
            line-height: 1.4;
            font-weight: 700;
        }
        .topic-meta {
            display: flex;
            flex-wrap: wrap;
            gap: 10px 22px;
            margin: 10px 0 14px;
            color: #3f3f3f;
            font-size: 16px;
            font-weight: 500;
            line-height: 1.6;
        }
        .topic-meta span {
            display: inline-flex;
            align-items: center;
            gap: 7px;
        }
        .topic-meta b {
            color: #078f48;
            font-size: 15px;
            font-weight: 700;
            letter-spacing: 0.02em;
        }
        .topic-copy p {
            font-size: 19px;
            line-height: 1.76;
        }
        .section-important {
            display: block;
            padding: 0;
            background: transparent;
        }
        .section-important .section-body p,
        .section-important .section-body li {
            color: var(--ink);
            font-size: 19px;
            line-height: 1.76;
            font-weight: 500;
        }
        .section-important .section-body ul {
            list-style: none;
            padding-left: 0;
        }
        .section-important .section-body li {
            position: relative;
            padding-left: 20px;
        }
        .section-important .section-body li::before {
            content: "";
            position: absolute;
            left: 1px;
            top: 0.72em;
            width: 6px;
            height: 6px;
            border-radius: 50%;
            background: var(--wechat-green);
        }
        .ranking-list {
            display: grid;
            grid-template-columns: 1fr 1fr;
            column-gap: 34px;
            row-gap: 18px;
        }
        .rank-row {
            display: grid;
            grid-template-columns: 34px minmax(0, 1fr);
            gap: 10px;
            align-items: center;
        }
        .rank-position {
            color: #8c8c8c;
            font-family: Georgia, serif;
            font-size: 18px;
            font-weight: 800;
            font-style: italic;
        }
        .rank-row:nth-child(-n+3) .rank-position { color: var(--wechat-green); }
        .rank-label {
            display: flex;
            justify-content: space-between;
            gap: 10px;
            align-items: baseline;
            margin-bottom: 7px;
        }
        .rank-label strong {
            min-width: 0;
            color: var(--ink);
            font-size: 16px;
            font-weight: 700;
            overflow-wrap: anywhere;
        }
        .rank-label span {
            flex: 0 0 auto;
            color: #737373;
            font-size: 12px;
        }
        .rank-bar {
            height: 6px;
            overflow: hidden;
            background: #e5e5e5;
            border-radius: 4px;
        }
        .rank-bar i {
            display: block;
            height: 100%;
            background: #707070;
            border-radius: inherit;
        }
        .rank-row:nth-child(-n+3) .rank-bar i { background: var(--wechat-green); }
        .section-general .section-body { padding: 0 4px; }
        .masthead {
            padding: 34px 0 24px;
            border-bottom: 1px solid var(--line);
        }
        .summary-meta {
            display: flex;
            flex-wrap: wrap;
            gap: 8px 0;
            margin-top: 14px;
            color: #737373;
            font-size: 14px;
            line-height: 1.5;
            font-weight: 600;
        }
        .summary-meta span + span::before {
            content: "·";
            margin: 0 11px;
            color: var(--wechat-green);
            font-weight: 900;
        }
        .section-overview {
            display: block;
            margin: 0;
            padding: 2px 0 2px 20px;
            background: transparent;
            color: var(--ink);
            border: 0;
            border-left: 3px solid var(--wechat-green);
        }
        .section-overview .section-heading { display: none; }
        .section-overview .section-body { padding: 0; }
        .section-overview .section-body::before { content: none; }
        .section-overview .section-body p {
            color: var(--ink);
            font-size: 21px;
            line-height: 1.72;
        }
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
    <div class="paper">
        <header class="masthead">
            <div class="title-layout">
                <h1>$title</h1>
            </div>
            <div class="summary-meta">
                $context_stat
                <span>$date</span>
                <span>$msg_count 条消息</span>
                $topic_stat
            </div>
        </header>
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
    timeout: float = 180.0,
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

输出格式要求:
- 只输出 Markdown 正文,不要添加解释、前言或结尾说明.
- 不要使用 ```markdown 或任何代码围栏包裹全文.
- 必须使用以下二级标题,且标题文字保持一致:
  ## 概览
  ## 话题分析
  ## 重要信息
- 话题分析下每个话题使用三级标题:
  ### 话题一: xxx
- 每个话题下使用无序列表,字段固定为:
  - **时间**:
  - **参与者**:
  - **内容**:
- 不要输出发言排行榜、活跃成员榜或总计信息,这部分由程序自动生成.

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
        response = client.post("/chat/completions", json=payload, timeout=timeout)
    else:
        response = httpx.post(f"{api_url}/chat/completions", headers=headers, json=payload, timeout=timeout)
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
    timeout: float = 180.0,
) -> str:
    """使用 LLM 按主题生成聊天记录总结"""
    system_prompt = """你是群聊记录检索整理助手.用户会给一个主题、问题或分析目标,请只根据聊天记录生成一份围绕该目标的中文 Markdown 答案.

输出格式要求:
- 只输出 Markdown 正文,不要添加解释、前言或结尾说明.
- 不要使用 ```markdown 或任何代码围栏包裹全文.

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
        timeout=timeout,
    )
    response.raise_for_status()
    data = response.json()
    return data["choices"][0]["message"]["content"].strip()


def _escape_and_bold(text: str) -> str:
    """先转义 HTML,再恢复 **bold** 为 <strong>"""
    escaped = html.escape(text)
    escaped = _replace_emoji_images(escaped)
    return re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', escaped)


def _section_class(title: str) -> str:
    if "概览" in title or "一句话结论" in title:
        return "overview"
    if "话题" in title:
        return "topics"
    if "重要" in title:
        return "important"
    if "排行" in title:
        return "ranking"
    return "general"


def _strip_markdown_fence(md_text: str) -> str:
    text = (md_text or "").strip()
    match = re.fullmatch(r"```(?:markdown|md)?\s*\n(?P<body>.*?)\n```", text, re.IGNORECASE | re.DOTALL)
    if match:
        return match.group("body").strip()
    return text


def _render_markdown_fragment(md_text: str) -> str:
    if not md_text.strip():
        return ""
    import markdown as markdown_lib

    rendered = markdown_lib.markdown(
        md_text,
        extensions=["extra", "sane_lists"],
        output_format="html5",
    )
    return _replace_emoji_images(rendered)


def _split_heading_blocks(lines: list[str], heading_prefix: str) -> list[tuple[str | None, list[str]]]:
    blocks: list[tuple[str | None, list[str]]] = []
    title: str | None = None
    body: list[str] = []

    for line in lines:
        stripped = line.strip()
        if stripped.startswith(heading_prefix):
            blocks.append((title, body))
            title = stripped[len(heading_prefix):].strip()
            body = []
            continue
        body.append(line)

    blocks.append((title, body))
    return [(block_title, block_body) for block_title, block_body in blocks if block_title or "\n".join(block_body).strip()]


def _render_topic_cards(lines: list[str]) -> str:
    parts: list[str] = []
    topics = [(title, body) for title, body in _split_heading_blocks(lines, "### ") if title]
    if not topics:
        return _render_markdown_fragment("\n".join(lines).strip())

    field_pattern = re.compile(r"^\s*[-*]\s+\*\*(时间|参与者|内容)\*\*\s*[:\uFF1A]?\s*(.*)$")
    for index, (title, body) in enumerate(topics, start=1):
        fields: dict[str, str] = {}
        remaining: list[str] = []
        for line in body:
            match = field_pattern.match(line)
            if match:
                fields[match.group(1)] = match.group(2).strip()
            elif line.strip():
                remaining.append(line)

        clean_title = re.sub(r"^话题(?:[一二三四五六七八九十百]+|\d+)\s*[:\uFF1A]\s*", "", title).strip() or title
        meta: list[str] = []
        if fields.get("时间"):
            meta.append(f'<span><b>时间</b>{_escape_and_bold(fields["时间"])}</span>')
        if fields.get("参与者"):
            meta.append(f'<span><b>参与者</b>{_escape_and_bold(fields["参与者"])}</span>')

        copy_parts: list[str] = []
        if fields.get("内容"):
            copy_parts.append(_render_markdown_fragment(fields["内容"]))
        if remaining:
            copy_parts.append(_render_markdown_fragment("\n".join(remaining)))

        meta_html = f'<div class="topic-meta">{"".join(meta)}</div>' if meta else ""
        copy_html = f'<div class="topic-copy">{"".join(copy_parts)}</div>' if copy_parts else ""
        parts.append(
            '<article class="topic-card">'
            f'<div class="topic-index">{index:02d}</div>'
            '<div class="topic-main">'
            f'<h3>{_escape_and_bold(clean_title)}</h3>'
            f'{meta_html}{copy_html}'
            '</div>'
            '</article>'
        )

    return f'<div class="topics-list">{"".join(parts)}</div>'


def _render_ranking(lines: list[str]) -> str:
    row_pattern = re.compile(
        r"^\s*-\s*.+?\s+\*\*(?P<name>.+?)\*\*\s*(?:-|—|\u2013)\s*"
        r"(?P<count>\d+)\s*条(?:消息)?\s*/\s*(?P<chars>\d+)\s*字\s*$"
    )
    rows: list[tuple[str, int, int]] = []

    for line in lines:
        match = row_pattern.match(line)
        if match:
            rows.append((match.group("name"), int(match.group("count")), int(match.group("chars"))))

    if not rows:
        return _render_markdown_fragment("\n".join(lines).strip())

    max_count = max(count for _, count, _ in rows)
    rendered_rows: list[str] = []
    for index, (name, count, _chars) in enumerate(rows, start=1):
        width = max(7, round(count / max_count * 100))
        rendered_rows.append(
            '<div class="rank-row">'
            f'<div class="rank-position">{index:02d}</div>'
            '<div class="rank-main">'
            f'<div class="rank-label"><strong>{_escape_and_bold(name)}</strong><span>{count} 条</span></div>'
            f'<div class="rank-bar"><i style="width:{width}%"></i></div>'
            '</div>'
            '</div>'
        )

    return f'<div class="ranking-list">{"".join(rendered_rows)}</div>'


def _render_section_body(lines: list[str], section_class: str) -> str:
    if section_class == "topics":
        return _render_topic_cards(lines)
    if section_class == "ranking":
        return _render_ranking(lines)

    parts: list[str] = []
    for title, body in _split_heading_blocks(lines, "### "):
        body_md = "\n".join(body).strip()
        if title is None:
            rendered = _render_markdown_fragment(body_md)
            if rendered:
                parts.append(rendered)
            continue

        topic_body = _render_markdown_fragment(body_md)
        if topic_body:
            parts.append(f'<article class="topic-card"><h3>{_escape_and_bold(title)}</h3>{topic_body}</article>')
        else:
            parts.append(f'<article class="topic-card"><h3>{_escape_and_bold(title)}</h3></article>')

    return "\n".join(parts)


def _render_report_section(title: str, body: list[str]) -> str:
    section_class = _section_class(title)
    if section_class == "important":
        plain_body = _strip_markdown("\n".join(body)).strip().rstrip("。.")
        if plain_body in {"无", "暂无", "没有", "无重要信息", "暂无重要信息"}:
            return ""
    body_html = _render_section_body(body, section_class)
    heading_html = ""
    if section_class != "overview":
        heading_html = f'<div class="section-heading"><h2>{_escape_and_bold(title)}</h2></div>'
    return (
        f'<section class="report-section section-{section_class}">'
        f'{heading_html}'
        f'<div class="section-body">{body_html}</div>'
        '</section>'
    )


def markdown_to_html(md_text: str) -> str:
    """将 Markdown 转换为 HTML,保留总结卡片结构."""
    md_text = _strip_markdown_fence(md_text)
    if not md_text:
        return ""

    sections: list[str] = []
    for title, body in _split_heading_blocks(md_text.splitlines(), "## "):
        if title is None:
            intro = _render_section_body(body, "general")
            if intro:
                sections.append(
                    '<section class="report-section section-overview">'
                    f'<div class="section-body">{intro}</div>'
                    '</section>'
                )
            continue
        section = _render_report_section(title, body)
        if section:
            sections.append(section)

    return "\n".join(sections)


def render_to_image(
    summary: str,
    date_str: str,
    msg_count: int,
    gen_time: str,
    output_path: str,
    group_name: str = "",
    title_override: str | None = None,
) -> bool:
    """使用配置的浏览器将总结渲染为图片,自动裁剪空白"""
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

    browser_executable = config.BROWSER_EXECUTABLE.strip()
    if not browser_executable:
        logger.error("渲染图片失败: BROWSER_EXECUTABLE 未配置")
        return False
    browser_path = os.path.abspath(os.path.expandvars(os.path.expanduser(browser_executable)))
    if not os.path.isfile(browser_path):
        logger.error("渲染图片失败: BROWSER_EXECUTABLE 文件不存在: %s", browser_path)
        return False

    html_content = markdown_to_html(summary)

    base_height = 500
    content_height = len(summary) * 1.6 + summary.count('\n') * 50
    estimated_height = int(base_height + content_height)
    render_height = max(estimated_height, 1500) + 800

    template = Template(HTML_TEMPLATE)
    safe_group = html.escape(group_name) if group_name else ""
    title = html.escape(title_override) if title_override else (safe_group or "今日群聊")
    title = _replace_emoji_images(title)
    topic_count = len(re.findall(r"^###\s+", summary, flags=re.MULTILINE))
    context_stat = f'<span>{safe_group}</span>' if title_override and safe_group else ""
    topic_stat = f'<span>{topic_count} 个话题</span>' if topic_count else ""
    full_html = template.safe_substitute(
        title=title,
        context_stat=context_stat,
        date=html.escape(date_str),
        msg_count=msg_count,
        topic_stat=topic_stat,
        content=html_content,
    )

    try:
        output_dir = os.path.dirname(output_path) or os.getcwd()
        output_dir = os.path.abspath(output_dir)
        os.makedirs(output_dir, exist_ok=True)

        output_name = os.path.basename(output_path)
        if not output_name.endswith('.png'):
            output_name += '.png'

        temp_name = f"_temp_{output_name}"
        temp_path = os.path.join(output_dir, temp_name)
        final_path = os.path.join(output_dir, output_name)

        if os.path.exists(temp_path):
            os.remove(temp_path)

        html_file_path = os.path.join(output_dir, f"_temp_{output_name}.html")
        with open(html_file_path, "w", encoding="utf-8") as f:
            f.write(full_html)

        file_url = Path(html_file_path).as_uri()
        logger.info("使用配置的浏览器渲染: %s", browser_path)
        hti = Html2Image(
            size=(900, render_height),
            browser_executable=browser_path,
            output_path=output_dir,
        )
        hti.screenshot(url=file_url, save_as=temp_name)

        for _ in range(10):
            if os.path.exists(temp_path):
                break
            time.sleep(0.5)

        if not os.path.exists(temp_path):
            logger.error("渲染图片失败: 临时文件未生成 %s", temp_path)
            return False

        img = Image.open(temp_path)
        pixels = img.load()
        width, height = img.size

        bottom = height
        center_x = width // 2
        bg_color = pixels[0, 0][:3]

        for y in range(height - 1, 100, -1):
            r, g, b = pixels[center_x, y][:3]
            if any(abs(channel - background) > 3 for channel, background in zip((r, g, b), bg_color, strict=True)):
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
