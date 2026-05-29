"""群聊总结服务 - 调 chatlog HTTP API 拉消息,本地跑 LLM + 渲染,通过 gateway_client 发出"""

import base64
import contextlib
import logging
import os
import re
import tempfile
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from common.config import config as app_config
from common.models import MessageType, OutgoingMessage
from cortex.gateway_client import GatewayClient
from cortex.services.chatlog_client import ChatlogClient
from cortex.services.summary import (
    build_summary_text,
    extract_overview,
    format_messages_for_llm,
    generate_ranking,
    generate_ranking_text,
    render_to_image,
    summarize_with_llm,
)

logger = logging.getLogger(__name__)


@dataclass
class SummaryGroup:
    group_id: str
    group_name: str


@dataclass
class SummaryConfig:
    groups: list[SummaryGroup]
    date: str | None = None  # YYYY-MM-DD,默认为今天


@dataclass
class SummaryResult:
    success: bool
    message: str
    details: dict | None = None


class SummaryService:
    """群聊总结服务 - 单例,串行执行"""

    def __init__(self, chatlog_client: ChatlogClient, gateway_client: GatewayClient):
        self._chatlog = chatlog_client
        self._gateway = gateway_client
        self._running_lock = threading.Lock()
        self._is_running = False
        self._worker_thread: threading.Thread | None = None
        self._llm_client: httpx.Client | None = None
        if app_config.OPENAI_API_KEY:
            self._llm_client = httpx.Client(
                base_url=app_config.OPENAI_BASE_URL,
                headers={
                    "Authorization": f"Bearer {app_config.OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0),
            )

    @property
    def is_running(self) -> bool:
        return self._is_running

    def close(self, timeout: float = 30) -> None:
        """等待运行中的 worker 完成,然后关闭 LLM 客户端"""
        if self._worker_thread and self._worker_thread.is_alive():
            logger.info("[Summary] 等待运行中的总结任务完成...")
            self._worker_thread.join(timeout=timeout)
        if self._llm_client:
            self._llm_client.close()

    def run_summary(self, config: SummaryConfig) -> SummaryResult:
        if not self._running_lock.acquire(blocking=False):
            return SummaryResult(success=False, message="已有总结任务正在运行,请稍后再试")
        self._is_running = True
        try:
            return self._execute(config)
        except Exception as e:
            logger.exception("[Summary] 总结执行失败")
            return SummaryResult(success=False, message=f"总结执行失败: {e}")
        finally:
            self._is_running = False
            self._running_lock.release()

    def start_summary_async(
        self,
        config: SummaryConfig,
        on_complete: Callable | None = None,
        *,
        decrypt_first: bool = True,
    ) -> SummaryResult:
        if not self._running_lock.acquire(blocking=False):
            return SummaryResult(success=False, message="已有总结任务正在运行,请稍后再试")
        self._is_running = True

        def run_and_release():
            try:
                result = self._execute(config, decrypt_first=decrypt_first)
                if on_complete:
                    on_complete(result)
            except Exception as e:
                logger.exception("[Summary] 总结执行失败")
                if on_complete:
                    on_complete(SummaryResult(success=False, message=f"总结执行失败: {e}"))
            finally:
                self._is_running = False
                self._running_lock.release()

        worker = threading.Thread(target=run_and_release, daemon=True, name="summary-worker")
        self._worker_thread = worker
        worker.start()
        return SummaryResult(success=True, message="总结任务已启动")

    def start_summary_for_group_name(
        self,
        group_name: str,
        *,
        log_prefix: str = "[Summary]",
    ) -> SummaryResult:
        normalized = group_name.strip()
        if not normalized:
            return SummaryResult(success=False, message="群名为空")

        try:
            decrypt_resp = self._chatlog.decrypt()
            logger.info(f"{log_prefix} 解密完成: {decrypt_resp}")
        except Exception as e:
            logger.error(f"{log_prefix} 解密失败: {e}")
            return SummaryResult(success=False, message=f"数据库解密失败: {e}")

        def group_names(group: dict) -> set[str]:
            names = {
                str(group.get("username") or "").strip(),
                str(group.get("display_name") or "").strip(),
                str(group.get("nick_name") or "").strip(),
            }
            return {name for name in names if name}

        matched = next((group for group in self._chatlog.list_groups() if normalized in group_names(group)), None)
        if not matched:
            return SummaryResult(success=False, message=f"chatlog 未找到群聊: {group_name}")

        group_id = str(matched.get("username") or "").strip()
        if not group_id:
            return SummaryResult(success=False, message=f"chatlog 群聊缺少 username: {group_name}")

        config = SummaryConfig(groups=[SummaryGroup(group_id=group_id, group_name=group_name)])

        def on_complete(result: SummaryResult):
            if result.success:
                logger.info(f"{log_prefix} 群聊总结完成: {result.message}")
                return
            logger.warning(f"{log_prefix} 群聊总结失败: {result.message}")

        result = self.start_summary_async(config, on_complete, decrypt_first=False)
        if not result.success:
            logger.warning(f"{log_prefix} 群聊总结启动失败: {result.message}")
        return result

    def _execute(self, config: SummaryConfig, *, decrypt_first: bool = True) -> SummaryResult:
        results: dict = {"decrypt": None, "groups": {}}

        # 1. 触发 chatlog 服务解密(chatlog 自己读 .env 里的路径/密钥)
        if decrypt_first:
            try:
                decrypt_resp = self._chatlog.decrypt()
                results["decrypt"] = decrypt_resp
                logger.info(f"[Summary] 解密完成: {decrypt_resp}")
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

        success_count = 0
        fail_count = 0
        skip_count = 0

        for group in config.groups:
            group_result = {"success": False, "message": "", "msg_count": 0}
            display_name = group.group_name

            try:
                # 取群显示名(chatlog 提供)
                try:
                    display_name = self._chatlog.get_group_display_name(group.group_id) or group.group_name
                except Exception as e:
                    logger.warning(f"[Summary] 获取群显示名失败 {group.group_id}: {e}")

                logger.info(f"[Summary] 处理群聊: {display_name} ({group.group_id})")

                # 取消息
                msg_dicts = self._chatlog.get_messages(
                    group=group.group_id,
                    start=start_time,
                    end=end_time,
                    limit=2000,
                )
                logger.info(f"[Summary] 获取到 {len(msg_dicts)} 条消息")

                if not msg_dicts:
                    group_result["message"] = "没有消息记录"
                    results["groups"][group.group_id] = group_result
                    skip_count += 1
                    continue

                messages_text, valid_count, sender_stats = format_messages_for_llm(msg_dicts)
                group_result["msg_count"] = valid_count
                if valid_count == 0:
                    group_result["message"] = "没有有效消息"
                    results["groups"][group.group_id] = group_result
                    skip_count += 1
                    continue

                ranking = generate_ranking(sender_stats)
                ranking_text = generate_ranking_text(sender_stats)

                logger.info("[Summary] 调用 LLM 生成总结...")
                try:
                    summary = summarize_with_llm(
                        messages_text=messages_text,
                        group_name=display_name,
                        date_str=date_str,
                        api_url=app_config.OPENAI_BASE_URL,
                        api_key=app_config.OPENAI_API_KEY,
                        model=app_config.OPENAI_MODEL,
                        client=self._llm_client,
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

                logger.info(f"[Summary] LLM 总结成功,长度: {len(summary)}")

                overview = extract_overview(summary)
                summary = summary + "\n\n" + ranking
                text_message = build_summary_text(overview, ranking_text, display_name)
                gen_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # 渲染图片到本地临时目录
                output_dir = tempfile.gettempdir()
                safe_group_id = re.sub(r'[^\w@.]', '_', group.group_id)
                output_image = os.path.join(output_dir, f"summary_{safe_group_id}_{file_date_str}.png")

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

                with open(output_image, "rb") as f:
                    image_b64 = base64.b64encode(f.read()).decode("utf-8")
                with contextlib.suppress(OSError):
                    os.remove(output_image)

                # 通过 gateway_client WS 发送
                self._gateway.send_message(OutgoingMessage(
                    group_name=group.group_name,
                    message_type=MessageType.IMAGE,
                    message=image_b64,
                ))
                if text_message:
                    self._gateway.send_message(OutgoingMessage(
                        group_name=group.group_name,
                        message_type=MessageType.TEXT,
                        message=text_message,
                    ))

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

        total = len(config.groups)
        if success_count == 0 and fail_count == 0:
            return SummaryResult(success=True, message=f"无有效消息: {skip_count}/{total} 个群聊跳过", details=results)
        if fail_count == 0:
            msg = f"全部完成: {success_count}/{total} 个群聊"
            if skip_count:
                msg += f", {skip_count} 个跳过(无消息)"
            return SummaryResult(success=True, message=msg, details=results)
        elif success_count > 0:
            return SummaryResult(
                success=True,
                message=f"部分完成: {success_count}/{total} 成功, {fail_count}/{total} 失败",
                details=results,
            )
        return SummaryResult(success=False, message=f"全部失败: {fail_count}/{total} 个群聊", details=results)
