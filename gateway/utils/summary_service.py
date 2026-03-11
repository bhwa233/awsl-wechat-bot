"""
群聊总结服务 - 直接调用内部模块，不走 HTTP
"""

import logging
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class SummaryGroup:
    group_id: str
    group_name: str


@dataclass
class SummaryConfig:
    input_path: str
    key: str
    output_path: str
    groups: list[SummaryGroup]
    send_fn: Callable[[str, str, bool], None] = field(default=lambda *a, **k: None)  # (group_name, content, is_image)
    date: str | None = None  # YYYY-MM-DD，默认为今天


@dataclass
class SummaryResult:
    success: bool
    message: str
    details: dict | None = None


class SummaryService:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._running_lock = threading.Lock()
        self._is_running = False
        self._initialized = True

    @property
    def is_running(self) -> bool:
        return self._is_running

    def run_summary(self, config: SummaryConfig) -> SummaryResult:
        if not self._running_lock.acquire(blocking=False):
            return SummaryResult(success=False, message="已有总结任务正在运行，请稍后再试")

        self._is_running = True
        try:
            return self._execute_summary(config)
        except Exception as e:
            logger.exception("[Summary] 总结执行失败")
            return SummaryResult(success=False, message=f"总结执行失败: {e}")
        finally:
            self._is_running = False
            self._running_lock.release()

    def start_summary_async(self, config: SummaryConfig, on_complete: Callable | None = None) -> SummaryResult:
        if not self._running_lock.acquire(blocking=False):
            return SummaryResult(success=False, message="已有总结任务正在运行，请稍后再试")

        self._is_running = True

        def run_and_release():
            try:
                result = self._execute_summary(config)
                if on_complete:
                    on_complete(result)
            except Exception as e:
                logger.exception("[Summary] 总结执行失败")
                if on_complete:
                    on_complete(SummaryResult(success=False, message=f"总结执行失败: {e}"))
            finally:
                self._is_running = False
                self._running_lock.release()

        threading.Thread(target=run_and_release, daemon=True).start()
        return SummaryResult(success=True, message="总结任务已启动")

    def _execute_summary(self, config: SummaryConfig) -> SummaryResult:
        import base64

        from gateway.utils.wechat_chatlog import WeChatDBDecryptor, WeChatDBReader
        from gateway.utils.chat_summary import (
            format_messages_for_llm,
            generate_ranking,
            generate_ranking_text,
            extract_overview,
            build_summary_text,
            summarize_with_llm,
            render_to_image,
        )
        from common.config import config as app_config

        results = {"decrypt": None, "groups": {}}

        # 1. 直接解密数据库
        logger.info(f"[Summary] 解密数据库: {config.input_path} -> {config.output_path}")
        try:
            decryptor = WeChatDBDecryptor(config.key)
            count = decryptor.decrypt_directory(config.input_path, config.output_path)
            results["decrypt"] = {"count": count}
            logger.info(f"[Summary] 解密完成: {count} 个文件")
        except Exception as e:
            logger.error(f"[Summary] 解密失败: {e}")
            return SummaryResult(success=False, message=f"数据库解密失败: {e}", details=results)

        # 2. 日期范围
        if config.date:
            try:
                target_date = datetime.strptime(config.date, "%Y-%m-%d")
            except ValueError:
                return SummaryResult(success=False, message=f"无效的日期格式: {config.date}")
            date_str = target_date.strftime("%Y-%m-%d")
            file_date_str = date_str
            start_time = target_date.replace(hour=5, minute=0, second=0)
            end_time = (target_date + timedelta(days=1)).replace(hour=5, minute=0, second=0)
        else:
            now = datetime.now()
            today_5am = now.replace(hour=5, minute=0, second=0, microsecond=0)
            start_time = today_5am if now >= today_5am else today_5am - timedelta(days=1)
            end_time = now
            date_str = f"{start_time.strftime('%Y-%m-%d %H:%M')} ~ {now.strftime('%Y-%m-%d %H:%M')}"
            file_date_str = now.strftime("%Y-%m-%d_%H%M")

        # 3. 打开数据库读取器
        reader = None
        try:
            reader = WeChatDBReader(config.output_path)
        except Exception as e:
            logger.warning(f"[Summary] 打开数据库失败: {e}")

        # 4. 逐群生成总结
        success_count = 0
        fail_count = 0

        for group in config.groups:
            display_name = group.group_name
            if reader:
                try:
                    display_name = reader.get_group_display_name(group.group_id) or group.group_name
                except Exception:
                    pass

            group_result = {"success": False, "message": "", "msg_count": 0}

            try:
                logger.info(f"[Summary] 处理群聊: {display_name} ({group.group_id})")

                # 直接读取消息
                if not reader:
                    group_result["message"] = "数据库不可用"
                    results["groups"][group.group_id] = group_result
                    fail_count += 1
                    continue

                messages = reader.get_messages(
                    talker=group.group_id,
                    start_time=start_time,
                    end_time=end_time,
                    text_only=True,
                    limit=2000,
                )
                # 转为 dict 列表（兼容 format_messages_for_llm）
                msg_dicts = [
                    {
                        "time": m.time.isoformat(),
                        "sender_name": m.sender_name,
                        "content": m.content,
                        "is_self": m.is_self,
                    }
                    for m in messages
                ]

                logger.info(f"[Summary] 获取到 {len(msg_dicts)} 条消息")

                if not msg_dicts:
                    group_result["message"] = "没有消息记录"
                    results["groups"][group.group_id] = group_result
                    continue

                messages_text, valid_count, sender_stats = format_messages_for_llm(msg_dicts)
                group_result["msg_count"] = valid_count

                if valid_count == 0:
                    group_result["message"] = "没有有效消息"
                    results["groups"][group.group_id] = group_result
                    continue

                ranking = generate_ranking(sender_stats)
                ranking_text = generate_ranking_text(sender_stats)

                # LLM 总结
                logger.info("[Summary] 调用 LLM 生成总结...")
                try:
                    summary = summarize_with_llm(
                        messages_text=messages_text,
                        group_name=display_name,
                        date_str=date_str,
                        api_url=app_config.OPENAI_BASE_URL,
                        api_key=app_config.OPENAI_API_KEY,
                        model=app_config.OPENAI_MODEL,
                    )
                except Exception as e:
                    logger.error(f"[Summary] LLM 总结失败: {e}")
                    group_result["message"] = f"LLM 总结失败: {e}"
                    results["groups"][group.group_id] = group_result
                    fail_count += 1
                    continue

                if not summary or len(summary.strip()) < 50:
                    group_result["message"] = "LLM 返回内容无效"
                    results["groups"][group.group_id] = group_result
                    fail_count += 1
                    continue

                logger.info(f"[Summary] LLM 总结成功，长度: {len(summary)}")

                overview = extract_overview(summary)
                summary = summary + "\n\n" + ranking
                text_message = build_summary_text(overview, ranking_text, display_name)
                gen_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # 渲染图片
                output_dir = os.path.abspath(config.output_path)
                os.makedirs(output_dir, exist_ok=True)
                output_image = os.path.join(output_dir, f"summary_{group.group_id}_{file_date_str}.png")

                if not render_to_image(summary, date_str, valid_count, gen_time, output_image, display_name):
                    group_result["message"] = "图片渲染失败"
                    results["groups"][group.group_id] = group_result
                    fail_count += 1
                    continue

                if not os.path.exists(output_image):
                    group_result["message"] = "图片文件不存在"
                    results["groups"][group.group_id] = group_result
                    fail_count += 1
                    continue

                # 通过回调发送（不走 HTTP）
                logger.info(f"[Summary] 发送到群聊: {group.group_name}")
                with open(output_image, "rb") as f:
                    image_b64 = base64.b64encode(f.read()).decode("utf-8")
                config.send_fn(group.group_name, image_b64, True)

                try:
                    config.send_fn(group.group_name, text_message, False)
                except Exception as e:
                    logger.warning(f"[Summary] 文本消息发送失败 [{group.group_name}]: {e}")

                # 清理图片
                try:
                    os.remove(output_image)
                except OSError:
                    pass

                group_result["success"] = True
                group_result["message"] = "成功"
                results["groups"][group.group_id] = group_result
                success_count += 1
                logger.info(f"[Summary] 群聊 {display_name} 总结完成")

            except Exception as e:
                logger.exception(f"[Summary] 处理群聊 {display_name} 失败")
                group_result["message"] = str(e)
                results["groups"][group.group_id] = group_result
                fail_count += 1

        if reader:
            try:
                reader.close()
            except Exception:
                pass

        total = len(config.groups)
        if fail_count == 0:
            return SummaryResult(success=True, message=f"全部完成: {success_count}/{total} 个群聊", details=results)
        elif success_count > 0:
            return SummaryResult(success=True, message=f"部分完成: {success_count}/{total} 成功, {fail_count}/{total} 失败", details=results)
        else:
            return SummaryResult(success=False, message=f"全部失败: {fail_count}/{total} 个群聊", details=results)


_summary_service = SummaryService()


def run_chat_summary(config: SummaryConfig) -> SummaryResult:
    return _summary_service.run_summary(config)


def start_chat_summary_async(config: SummaryConfig, on_complete: Callable | None = None) -> SummaryResult:
    return _summary_service.start_summary_async(config, on_complete)


def is_summary_running() -> bool:
    return _summary_service.is_running
