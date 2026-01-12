#!/usr/bin/env python3
"""
群聊工具 - 微信聊天记录解密与总结

子命令:
    decrypt  - 解密微信数据库
    summary  - 生成群聊总结

使用方法:
    python chat_summary.py decrypt --input <输入路径> --key <密钥> --output <输出路径>
    python chat_summary.py summary --group <群ID> --db-path <数据库路径> [--date <日期>] [--image]

示例:
    # 解密数据库
    python chat_summary.py decrypt \\
        --input "C:\\Users\\xxx\\Documents\\xwechat_files\\wxid_xxx" \\
        --key "9d0391daa25847..." \\
        --output "C:\\decrypted"

    # 生成总结
    python chat_summary.py summary --group 49100408389@chatroom --db-path "C:\\decrypted"
    python chat_summary.py summary --group 49100408389@chatroom --db-path "C:\\decrypted" --image
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from typing import Optional

import requests

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import config

# HTML 模板，用于渲染 Markdown 成图片（使用 $var 占位符避免与 CSS 花括号冲突）
HTML_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
    <meta charset="UTF-8">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            background: #f5f5f5;
            padding: 32px;
            min-height: 100vh;
        }
        .container {
            max-width: 800px;
            margin: 0 auto;
            background: white;
            border-radius: 8px;
            box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
            overflow: hidden;
        }
        .header {
            background: #1a1a2e;
            color: white;
            padding: 28px 36px;
        }
        .header h1 { font-size: 22px; font-weight: 600; margin-bottom: 12px; }
        .header .meta { font-size: 13px; opacity: 0.8; }
        .header .meta span { margin-right: 24px; }
        .content { padding: 36px; color: #333; line-height: 1.75; font-size: 15px; }
        .content h1 { display: none; }
        .content h2 {
            color: #1a1a2e;
            font-size: 17px;
            font-weight: 600;
            margin: 28px 0 14px 0;
            padding-bottom: 8px;
            border-bottom: 1px solid #eee;
        }
        .content h2:first-child { margin-top: 0; }
        .content h3 { color: #444; font-size: 15px; font-weight: 600; margin: 20px 0 10px 0; }
        .content p { margin: 10px 0; text-align: justify; }
        .content ul, .content ol { margin: 10px 0 10px 20px; }
        .content li { margin: 6px 0; }
        .content strong { color: #1a1a2e; font-weight: 600; }
        .content hr { display: none; }
        .footer {
            background: #fafafa;
            padding: 16px 36px;
            text-align: right;
            color: #999;
            font-size: 11px;
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


def fetch_messages(
    api_base: str,
    db_path: str,
    group: str,
    start: str,
    end: str,
    limit: int = 1000,
    token: Optional[str] = None
) -> list[dict]:
    """从 API 获取聊天记录"""
    url = f"{api_base}/api/chatlog/messages"
    params = {
        "db_path": db_path,
        "group": group,
        "start": start,
        "end": end,
        "limit": limit
    }
    headers = {"accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    response = requests.get(url, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def decrypt_database(
    api_base: str,
    input_path: str,
    key: str,
    output_path: str,
    token: Optional[str] = None
) -> dict:
    """调用 API 解密微信数据库"""
    url = f"{api_base}/api/chatlog/decrypt"
    headers = {
        "accept": "application/json",
        "Content-Type": "application/json"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "input_path": input_path,
        "key": key,
        "output_path": output_path
    }

    response = requests.post(url, headers=headers, json=payload, timeout=300)
    response.raise_for_status()
    return response.json()


def format_messages_for_llm(messages: list[dict]) -> tuple[str, int, dict[str, tuple[int, int]]]:
    """将消息格式化为 LLM 可读的文本，并统计发送者消息数量和字数"""
    if not messages:
        return "（无消息记录）", 0, {}

    lines = []
    sender_stats: dict[str, tuple[int, int]] = {}  # sender -> (消息数, 字数)

    for msg in messages:
        if msg.get("is_self"):
            continue
        time_str = msg.get("time", "")[11:19]
        sender = msg.get("sender_name") or "未知"
        content = msg.get("content", "")
        lines.append(f"[{time_str}] {sender}: {content}")

        # 统计消息数量和字数
        count, chars = sender_stats.get(sender, (0, 0))
        sender_stats[sender] = (count + 1, chars + len(content))

    return "\n".join(lines), len(lines), sender_stats


def generate_ranking(sender_stats: dict[str, tuple[int, int]], top_n: int = 10) -> str:
    """生成消息排行榜 Markdown"""
    if not sender_stats:
        return ""

    # 按消息数量排序
    sorted_senders = sorted(sender_stats.items(), key=lambda x: x[1][0], reverse=True)[:top_n]

    # 排名标识
    rank_icons = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

    # 计算总字数
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
    """从总结 Markdown 中提取“概览”段落"""
    import re

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

    section = "\n".join(lines[start:end]).strip()
    return section


def _strip_markdown(text: str) -> str:
    import re
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
    parts = [
        header,
        "",
        "概览：",
        overview_text,
        ""
    ]
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
    model: str = "gpt-4o-mini"
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

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ],
        "max_tokens": 2000,
        "temperature": 0.3
    }

    response = requests.post(
        f"{api_url}/chat/completions",
        headers=headers,
        json=payload,
        timeout=60
    )
    response.raise_for_status()

    data = response.json()
    return data["choices"][0]["message"]["content"].strip()


def _get_group_display_name(db_path: str, group_id: str) -> str:
    try:
        from src.utils.wechat_chatlog import WeChatDBReader
    except Exception:
        return ""

    reader = None
    try:
        reader = WeChatDBReader(db_path)
        return reader.get_group_display_name(group_id)
    except Exception:
        return ""
    finally:
        if reader:
            try:
                reader.close()
            except Exception:
                pass


def markdown_to_html(md_text: str) -> str:
    """将 Markdown 转换为 HTML"""
    try:
        import markdown
        return markdown.markdown(md_text, extensions=['tables', 'fenced_code'])
    except ImportError:
        # 简单的 Markdown 转换
        import re
        lines = md_text.split('\n')
        html_lines = []
        in_list = False

        for line in lines:
            stripped = line.strip()

            # 标题
            if stripped.startswith('### '):
                if in_list:
                    html_lines.append('</ul>')
                    in_list = False
                html_lines.append(f'<h3>{stripped[4:]}</h3>')
            elif stripped.startswith('## '):
                if in_list:
                    html_lines.append('</ul>')
                    in_list = False
                html_lines.append(f'<h2>{stripped[3:]}</h2>')
            elif stripped.startswith('# '):
                if in_list:
                    html_lines.append('</ul>')
                    in_list = False
                html_lines.append(f'<h1>{stripped[2:]}</h1>')
            # 列表项
            elif stripped.startswith('- '):
                if not in_list:
                    html_lines.append('<ul>')
                    in_list = True
                content = stripped[2:]
                content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content)
                html_lines.append(f'<li>{content}</li>')
            # 空行
            elif not stripped:
                if in_list:
                    html_lines.append('</ul>')
                    in_list = False
            # 普通段落
            else:
                if in_list:
                    html_lines.append('</ul>')
                    in_list = False
                content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', stripped)
                html_lines.append(f'<p>{content}</p>')

        if in_list:
            html_lines.append('</ul>')

        return '\n'.join(html_lines)


def render_to_image(
    summary: str,
    date_str: str,
    msg_count: int,
    gen_time: str,
    output_path: str,
    group_name: str = ""
) -> bool:
    """使用 html2image 将总结渲染为图片，自动裁剪空白"""
    try:
        from html2image import Html2Image
    except ImportError:
        print("错误: 需要安装 html2image: pip install html2image")
        return False

    try:
        from PIL import Image
    except ImportError:
        print("错误: 需要安装 Pillow: pip install Pillow")
        return False

    from string import Template
    import shutil

    # 将 markdown 转为 HTML
    html_content = markdown_to_html(summary)

    # 根据内容估算高度（每个字符约 0.5-1px，加上基础高度）
    base_height = 300  # header + footer + padding
    content_height = len(summary) * 0.8 + summary.count('\n') * 25
    estimated_height = int(base_height + content_height)
    # 设置一个足够大的高度，确保不截断
    render_height = max(estimated_height, 1000) + 500

    # 生成完整 HTML（使用 Template 避免花括号冲突）
    template = Template(HTML_TEMPLATE)
    import html
    safe_group = html.escape(group_name) if group_name else ""
    title = f"【{safe_group}】群聊总结" if safe_group else "群聊总结"
    full_html = template.safe_substitute(
        title=title,
        date=date_str,
        msg_count=msg_count,
        gen_time=gen_time,
        content=html_content
    )

    try:
        # 检测可用的浏览器
        browser_path = None

        # Windows Edge 路径
        edge_paths = [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]

        # 优先使用 Chrome，如果没有则使用 Edge
        if not shutil.which("chrome") and not shutil.which("google-chrome"):
            for edge_path in edge_paths:
                if os.path.exists(edge_path):
                    browser_path = edge_path
                    print(f"使用 Edge 浏览器: {edge_path}")
                    break

        # 确保输出目录是绝对路径且存在
        output_dir = os.path.dirname(output_path)
        if not output_dir:
            output_dir = os.getcwd()
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

        # 删除可能存在的旧临时文件
        if os.path.exists(temp_path):
            os.remove(temp_path)

        # 将 HTML 写入临时文件（避免 html2image 内部临时文件被删除的问题）
        html_file_path = os.path.join(output_dir, f"_temp_{output_name}.html")
        with open(html_file_path, "w", encoding="utf-8") as f:
            f.write(full_html)

        # 使用文件 URL 渲染截图（而非 html_str，避免 ERR_FILE_NOT_FOUND）
        file_url = f"file:///{html_file_path.replace(os.sep, '/')}"
        hti.screenshot(url=file_url, save_as=temp_name)

        # 等待文件生成
        import time
        for _ in range(10):
            if os.path.exists(temp_path):
                break
            time.sleep(0.5)

        if not os.path.exists(temp_path):
            print(f"渲染图片失败: 临时文件未生成 {temp_path}")
            return False

        # 裁剪空白部分
        img = Image.open(temp_path)
        pixels = img.load()
        width, height = img.size

        # 从底部向上扫描，找到容器底部
        # 背景色是 #f5f5f5 (245, 245, 245)，容器内是白色或浅灰 #fafafa
        # 找到第一个非背景色的像素位置
        bottom = height
        center_x = width // 2  # 检测中心位置
        bg_color = (245, 245, 245)  # #f5f5f5

        for y in range(height - 1, 100, -1):
            r, g, b = pixels[center_x, y][:3]
            # 检测到非背景色（容器内的颜色）
            if not (r == bg_color[0] and g == bg_color[1] and b == bg_color[2]):
                bottom = y + 40  # 留 40px 底部边距
                break

        # 裁剪图片
        bottom = min(bottom, height)
        if bottom < height:
            img = img.crop((0, 0, width, bottom))

        img.save(final_path)
        img.close()

        # 删除临时文件
        if os.path.exists(temp_path) and temp_path != final_path:
            os.remove(temp_path)
        if os.path.exists(html_file_path):
            os.remove(html_file_path)

        return True
    except Exception as e:
        print(f"渲染图片失败: {e}")
        return False


def send_image_to_group(
    api_base: str,
    group_name: str,
    image_path: str,
    token: Optional[str] = None
) -> bool:
    """发送图片到群聊"""
    import base64

    # 读取图片并转为 base64
    with open(image_path, "rb") as f:
        image_base64 = base64.b64encode(f.read()).decode("utf-8")

    url = f"{api_base}/api/send"
    headers = {
        "accept": "application/json",
        "Content-Type": "application/json"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "group_name": group_name,
        "image_base64": image_base64
    }

    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    return True


def send_text_to_group(
    api_base: str,
    group_name: str,
    message: str,
    token: Optional[str] = None
) -> bool:
    """发送文本到群聊"""
    url = f"{api_base}/api/send"
    headers = {
        "accept": "application/json",
        "Content-Type": "application/json"
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    payload = {
        "group_name": group_name,
        "message": message
    }

    response = requests.post(url, headers=headers, json=payload, timeout=30)
    response.raise_for_status()
    return True


def cmd_decrypt(args) -> int:
    """解密子命令"""
    print(f"输入路径: {args.input}")
    print(f"输出路径: {args.output}")
    print(f"API: {args.api_base}")
    print("-" * 40)

    print("正在解密数据库...")
    try:
        result = decrypt_database(
            api_base=args.api_base,
            input_path=args.input,
            key=args.key,
            output_path=args.output,
            token=args.token
        )
        print(f"解密完成: {result}")
        return 0
    except requests.exceptions.RequestException as e:
        print(f"错误: 解密失败: {e}")
        return 1


def cmd_summary(args) -> int:
    """总结子命令"""
    # 确定日期范围
    if args.date:
        try:
            target_date = datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            print(f"错误: 无效的日期格式: {args.date}")
            return 1
    else:
        target_date = datetime.now()

    date_str = target_date.strftime("%Y-%m-%d")
    next_date_str = (target_date + timedelta(days=1)).strftime("%Y-%m-%d")
    start_time = f"{date_str} 05:00:00"
    end_time = f"{next_date_str} 05:00:00"

    if not args.db_path:
        print("错误: 请提供 --db-path 参数")
        return 1

    if not config.OPENAI_API_KEY:
        print("错误: 未配置 OPENAI_API_KEY")
        return 1

    print(f"群聊ID: {args.group}")
    print(f"日期: {date_str}")
    print(f"数据库: {args.db_path}")
    print(f"API: {args.api_base}")
    if args.send:
        print(f"发送到: {args.send}")
    print("-" * 40)

    # 获取聊天记录
    print("正在获取聊天记录...")
    try:
        messages = fetch_messages(
            api_base=args.api_base,
            db_path=args.db_path,
            group=args.group,
            start=start_time,
            end=end_time,
            limit=args.limit,
            token=args.token
        )
    except requests.exceptions.RequestException as e:
        print(f"错误: 获取聊天记录失败: {e}")
        return 1

    print(f"获取到 {len(messages)} 条消息")

    if not messages:
        print("没有消息记录，无需总结")
        return 0

    messages_text, valid_count, sender_stats = format_messages_for_llm(messages)
    print(f"有效消息: {valid_count} 条（已过滤自己发送的消息）")

    if valid_count == 0:
        print("没有有效消息，无需总结")
        return 0

    # 生成排行榜
    ranking = generate_ranking(sender_stats)
    ranking_text = generate_ranking_text(sender_stats)

    # 生成总结
    print("正在生成总结...")
    display_group = _get_group_display_name(args.db_path, args.group) or args.group
    try:
        summary = summarize_with_llm(
            messages_text=messages_text,
            group_name=display_group,
            date_str=date_str,
            api_url=config.OPENAI_BASE_URL,
            api_key=config.OPENAI_API_KEY,
            model=config.OPENAI_MODEL
        )
    except requests.exceptions.RequestException as e:
        print(f"错误: LLM 请求失败: {e}")
        return 1

    # 合并总结和排行榜
    summary = summary + "\n\n" + ranking

    gen_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 如果需要发送，强制生成图片
    if args.send:
        print("正在渲染图片...")
        output_path = args.output or f"/tmp/summary_{date_str}.png"
        if not output_path.endswith('.png'):
            output_path += '.png'

        if not render_to_image(summary, date_str, valid_count, gen_time, output_path, display_group):
            return 1

        print(f"图片已保存到: {output_path}")

        # 发送图片
        print(f"正在发送图片到群聊: {args.send}")
        try:
            send_image_to_group(
                api_base=args.api_base,
                group_name=args.send,
                image_path=output_path,
                token=args.token
            )
            print("图片发送成功!")
            overview = extract_overview(summary)
            text_message = build_summary_text(overview, ranking_text, display_group)
            try:
                send_text_to_group(
                    api_base=args.api_base,
                    group_name=args.send,
                    message=text_message,
                    token=args.token
                )
                print("文本发送成功!")
            except requests.exceptions.RequestException as e:
                print(f"警告: 文本发送失败: {e}")
        except requests.exceptions.RequestException as e:
            print(f"错误: 发送图片失败: {e}")
            return 1
    elif args.image:
        # 仅生成图片不发送
        print("正在渲染图片...")
        output_path = args.output or f"summary_{date_str}.png"
        if not output_path.endswith('.png'):
            output_path += '.png'

        if render_to_image(summary, date_str, valid_count, gen_time, output_path, display_group):
            print(f"\n图片已保存到: {output_path}")
        else:
            return 1
    else:
        # 输出 Markdown
        title = f"群聊总结 - {display_group}" if display_group else "群聊总结"
        header = f"""# {title}

- **日期**: {date_str}
- **消息数**: {valid_count}
- **生成时间**: {gen_time}

---

"""
        full_summary = header + summary

        if args.output:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(full_summary)
            print(f"\n总结已保存到: {args.output}")
        else:
            print("\n" + "=" * 40)
            print(full_summary)

    return 0


def main():
    parser = argparse.ArgumentParser(
        description="群聊工具 - 微信聊天记录解密与总结",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # decrypt 子命令
    decrypt_parser = subparsers.add_parser("decrypt", help="解密微信数据库")
    decrypt_parser.add_argument("--input", "-i", required=True, help="输入路径（微信数据目录）")
    decrypt_parser.add_argument("--key", "-k", required=True, help="解密密钥")
    decrypt_parser.add_argument("--output", "-o", required=True, help="输出路径")
    decrypt_parser.add_argument("--api-base", default="http://localhost:8000", help="API 基础地址")
    decrypt_parser.add_argument("--token", help="API Bearer Token")

    # summary 子命令
    summary_parser = subparsers.add_parser("summary", help="生成群聊总结")
    summary_parser.add_argument("--group", "-g", required=True, help="群聊ID")
    summary_parser.add_argument("--date", "-d", help="日期 (YYYY-MM-DD)，默认为今天")
    summary_parser.add_argument("--db-path", required=True, help="解密后的数据库目录")
    summary_parser.add_argument("--api-base", default="http://localhost:8000", help="API 基础地址")
    summary_parser.add_argument("--token", help="API Bearer Token")
    summary_parser.add_argument("--output", "-o", help="输出文件路径")
    summary_parser.add_argument("--limit", "-n", type=int, default=2000, help="消息数量限制 (默认: 2000)")
    summary_parser.add_argument("--image", action="store_true", help="输出为图片")
    summary_parser.add_argument("--send", "-s", metavar="GROUP_NAME", help="生成后发送图片到指定群聊名称")

    args = parser.parse_args()

    if args.command == "decrypt":
        return cmd_decrypt(args)
    elif args.command == "summary":
        return cmd_summary(args)
    else:
        parser.print_help()
        return 1


if __name__ == "__main__":
    sys.exit(main())
