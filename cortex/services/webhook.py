"""
Webhook 服务
收到新消息后向配置的 URL 发送 POST 请求
支持群聊名称过滤和消息正则过滤
"""

import json
import logging
import queue
import re
import threading
import time
from collections import deque
from datetime import datetime
from enum import StrEnum
from urllib.parse import urlparse

import httpx
from jsonpath_ng.exceptions import JsonPathParserError
from jsonpath_ng.ext import parse as parse_jsonpath
from pydantic import BaseModel, field_validator, model_validator
from sqlmodel import Field, Session, SQLModel, create_engine, select

from common.config import config
from common.models import MAX_REGEX_LEN, OutgoingMessage, WSMessageBroadcast
from common.sqlite_migrator import SQLiteMigrator
from cortex.metrics import metrics
from cortex.models import MessageListener

logger = logging.getLogger(__name__)
_INVALID_JSON = object()
_STOP_WEBHOOK_TASK = object()
WebhookTask = tuple["Webhook", str, str]


def _now_local() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _preview_text(value: str, limit: int) -> str:
    return value if len(value) <= limit else f"{value[:limit]}..."


def _validate_webhook_url(v: str) -> str:
    parsed = urlparse(v)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError("Webhook URL 仅支持 http/https 且必须包含 host")

    if not parsed.hostname:
        raise ValueError("Webhook URL 主机解析失败")

    return v


def _validate_jsonpath_expr(expr: str) -> str:
    path = expr.strip()
    if not path:
        raise ValueError("reply_jsonpath 不能为空")
    try:
        parse_jsonpath(path)
    except JsonPathParserError as e:
        raise ValueError(f"reply_jsonpath 非法: {e}") from e
    return path


def _stringify_jsonpath_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    return json.dumps(value, ensure_ascii=False)


# ==================== Models ====================


class ReplyMode(StrEnum):
    NONE = "none"
    TEXT = "text"
    JSONPATH = "jsonpath"


class WebhookBase(SQLModel):
    name: str
    url: str
    headers: str = "{}"
    body_template: str = "{}"
    group_filter: str = ""
    message_filter: str = ""
    enabled: bool = True
    reply_mode: str | None = ReplyMode.NONE.value
    reply_jsonpath: str | None = ""

    @field_validator("reply_mode")
    @classmethod
    def validate_reply_mode(cls, v):
        if v is not None and v not in {e.value for e in ReplyMode}:
            raise ValueError(f"reply_mode 必须是 {[e.value for e in ReplyMode]} 之一")
        return v

    @model_validator(mode="after")
    def validate_reply_jsonpath(self):
        if self.reply_mode == ReplyMode.JSONPATH.value:
            self.reply_jsonpath = _validate_jsonpath_expr(self.reply_jsonpath or "")
        elif self.reply_jsonpath:
            self.reply_jsonpath = self.reply_jsonpath.strip()
        return self


class Webhook(WebhookBase, table=True):
    id: int | None = Field(default=None, primary_key=True)
    created_at: str | None = Field(default_factory=_now_local)
    updated_at: str | None = Field(default_factory=_now_local)


class WebhookCreate(WebhookBase):
    @field_validator("url")
    @classmethod
    def validate_url_scheme(cls, v):
        return _validate_webhook_url(v)


class WebhookUpdate(SQLModel):
    name: str | None = None
    url: str | None = None
    headers: str | None = None
    body_template: str | None = None
    group_filter: str | None = None
    message_filter: str | None = None
    enabled: bool | None = None
    reply_mode: str | None = None
    reply_jsonpath: str | None = None

    @field_validator("url")
    @classmethod
    def validate_url_scheme(cls, v):
        if v is not None:
            _validate_webhook_url(v)
        return v

    @field_validator("reply_mode")
    @classmethod
    def validate_reply_mode(cls, v):
        if v is not None and v not in {e.value for e in ReplyMode}:
            raise ValueError(f"reply_mode 必须是 {[e.value for e in ReplyMode]} 之一")
        return v

    @model_validator(mode="after")
    def validate_reply_jsonpath(self):
        if self.reply_mode == ReplyMode.JSONPATH.value:
            self.reply_jsonpath = _validate_jsonpath_expr(self.reply_jsonpath or "")
        elif self.reply_jsonpath is not None:
            self.reply_jsonpath = self.reply_jsonpath.strip()
        if self.reply_mode == ReplyMode.JSONPATH.value and self.reply_jsonpath == "":
            raise ValueError("reply_mode=jsonpath 时必须提供 reply_jsonpath")
        return self


class WebhookResponse(BaseModel):
    id: int
    name: str
    url: str
    headers: str
    body_template: str
    group_filter: str
    message_filter: str
    enabled: bool
    reply_mode: str | None = ReplyMode.NONE.value
    reply_jsonpath: str | None = ""
    created_at: str | None = None
    updated_at: str | None = None


# ==================== Service ====================


class WebhookService(MessageListener):
    """Webhook 服务 — SQLModel ORM + fire-and-forget 发送"""

    _REPLY_PREVIEW_LEN = 300

    def __init__(self, db_path: str, gateway_client=None):
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            echo=False,
        )
        Webhook.__table__.create(self.engine, checkfirst=True)
        self._run_migrations(db_path)
        self._task_queue: queue.Queue[WebhookTask | object] = queue.Queue(
            maxsize=config.WEBHOOK_QUEUE_SIZE
        )
        self._workers: list[threading.Thread] = []
        self._http_client = httpx.Client(timeout=config.WEBHOOK_TIMEOUT)
        self._gateway_client = gateway_client
        self._cache: list[Webhook] = []
        self._cache_ts: float = 0
        self._cache_ttl: float = config.WEBHOOK_CACHE_TTL
        self._running = False
        self._cache_lock = threading.Lock()
        self._dispatch_lock = threading.Lock()
        self._active_webhooks: set[int] = set()
        self._pending_tasks: dict[int, deque[WebhookTask]] = {}
        self._queued_task_count = 0
        self._jsonpath_cache: dict[tuple[int, str], object] = {}
        self._jsonpath_cache_lock = threading.Lock()
        for idx in range(config.WEBHOOK_POOL_SIZE):
            worker = threading.Thread(
                target=self._worker_loop,
                daemon=True,
                name=f"webhook-worker-{idx + 1}",
            )
            worker.start()
            self._workers.append(worker)
        self._running = True
        logger.info("Webhook 数据库初始化完成")

    def _run_migrations(self, db_path: str):
        migrator = SQLiteMigrator(db_path)

        def add_reply_mode(conn):
            SQLiteMigrator.add_column_if_missing(
                conn,
                "webhook",
                f"reply_mode TEXT NOT NULL DEFAULT '{ReplyMode.NONE.value}'",
                "reply_mode",
            )

        def add_reply_jsonpath(conn):
            SQLiteMigrator.add_column_if_missing(
                conn,
                "webhook",
                "reply_jsonpath TEXT NOT NULL DEFAULT ''",
                "reply_jsonpath",
            )

        migrator.migrate([
            ("20260331_001_webhook_add_reply_mode", add_reply_mode),
            ("20260331_002_webhook_add_reply_jsonpath", add_reply_jsonpath),
        ])

    # ==================== CRUD ====================

    def create(self, data: WebhookCreate) -> Webhook:
        with Session(self.engine) as session:
            webhook = Webhook.model_validate(data)
            session.add(webhook)
            session.commit()
            session.refresh(webhook)
            self._invalidate_cache()
            return webhook

    def get(self, webhook_id: int) -> Webhook | None:
        with Session(self.engine) as session:
            return session.get(Webhook, webhook_id)

    def get_all(self) -> list[Webhook]:
        with Session(self.engine) as session:
            return list(session.exec(select(Webhook).order_by(Webhook.id.desc())).all())

    def update(self, webhook_id: int, data: WebhookUpdate) -> Webhook | None:
        with Session(self.engine) as session:
            webhook = session.get(Webhook, webhook_id)
            if not webhook:
                return None
            old_reply_jsonpath = webhook.reply_jsonpath or ""
            update_data = data.model_dump(exclude_unset=True)
            webhook.sqlmodel_update(update_data)
            webhook.updated_at = _now_local()
            session.add(webhook)
            session.commit()
            session.refresh(webhook)
            self._invalidate_cache()
            if old_reply_jsonpath and old_reply_jsonpath != (webhook.reply_jsonpath or ""):
                with self._jsonpath_cache_lock:
                    self._jsonpath_cache.pop((webhook_id, old_reply_jsonpath), None)
            return webhook

    def delete(self, webhook_id: int) -> bool:
        with Session(self.engine) as session:
            webhook = session.get(Webhook, webhook_id)
            if not webhook:
                return False
            session.delete(webhook)
            session.commit()
            self._invalidate_cache()
            with self._dispatch_lock:
                self._active_webhooks.discard(webhook_id)
                dropped_tasks = self._pending_tasks.pop(webhook_id, None)
                if dropped_tasks:
                    self._queued_task_count = max(self._queued_task_count - len(dropped_tasks), 0)
            with self._jsonpath_cache_lock:
                for cache_key in [key for key in self._jsonpath_cache if key[0] == webhook_id]:
                    self._jsonpath_cache.pop(cache_key, None)
            return True

    # ==================== 触发 ====================

    def _invalidate_cache(self):
        with self._cache_lock:
            self._cache_ts = 0

    def _get_cached_all(self) -> list[Webhook]:
        with self._cache_lock:
            if time.time() - self._cache_ts < self._cache_ttl:
                return list(self._cache)
        webhooks = self.get_all()
        with self._cache_lock:
            self._cache = webhooks
            self._cache_ts = time.time()
        return list(webhooks)

    def _safe_regex_match(self, pattern: str, text: str) -> bool | None:
        """安全正则匹配,返回 None 表示正则无效"""
        if len(pattern) > MAX_REGEX_LEN:
            return None
        try:
            return bool(re.search(pattern, text))
        except re.error:
            return None

    def _handle_message(self, msg: WSMessageBroadcast) -> None:
        self.fire(msg.group_name, msg.content)

    def _enqueue_send(self, wh: Webhook, group_name: str, message: str) -> bool:
        webhook_id = wh.id
        if webhook_id is None:
            metrics.increment("webhook_enqueues_total", group=group_name, result="invalid")
            logger.warning(f"Webhook [{wh.name}] 缺少 ID,丢弃任务: {group_name}")
            return False

        task: WebhookTask = (wh, group_name, message)
        with self._dispatch_lock:
            if self._queued_task_count >= config.WEBHOOK_QUEUE_SIZE:
                metrics.increment("webhook_enqueues_total", group=group_name, result="queue_full")
                logger.warning(f"Webhook [{wh.name}] 队列已满,丢弃任务: {group_name}")
                return False

            if webhook_id in self._active_webhooks:
                self._pending_tasks.setdefault(webhook_id, deque()).append(task)
                self._queued_task_count += 1
                metrics.increment("webhook_enqueues_total", group=group_name, result="accepted")
                return True

            try:
                self._task_queue.put_nowait(task)
            except queue.Full:
                metrics.increment("webhook_enqueues_total", group=group_name, result="queue_full")
                logger.warning(f"Webhook [{wh.name}] worker 队列已满,丢弃任务: {group_name}")
                return False

            self._active_webhooks.add(webhook_id)
            self._queued_task_count += 1
            metrics.increment("webhook_enqueues_total", group=group_name, result="accepted")
            return True

    def fire(self, group_name: str, message: str):
        """异步触发所有已启用且匹配过滤条件的 webhook"""
        try:
            webhooks = self._get_cached_all()
        except Exception as e:
            logger.warning(f"获取 webhook 列表失败: {e}")
            return

        for wh in webhooks:
            if not wh.enabled:
                continue
            if wh.group_filter:
                match = self._safe_regex_match(wh.group_filter, group_name)
                if match is None:
                    logger.warning(f"Webhook [{wh.name}] group_filter 正则无效: {wh.group_filter}")
                    continue
                if not match:
                    continue
            if wh.message_filter:
                match = self._safe_regex_match(wh.message_filter, message)
                if match is None:
                    logger.warning(f"Webhook [{wh.name}] message_filter 正则无效: {wh.message_filter}")
                    continue
                if not match:
                    continue
            self._enqueue_send(wh, group_name, message)

    def test(self, wh: Webhook):
        """用固定示例数据测试单个 webhook"""
        return self._enqueue_send(wh, "测试群聊", "这是一条测试消息")

    def _worker_loop(self):
        while True:
            try:
                task = self._task_queue.get(timeout=1)
            except queue.Empty:
                if self._running:
                    continue
                break

            if task is _STOP_WEBHOOK_TASK:
                self._task_queue.task_done()
                break

            while True:
                wh, group_name, message = task
                metrics.increment("webhook_workers_busy", group=group_name)
                try:
                    self._send(wh, group_name, message)
                finally:
                    metrics.decrement("webhook_workers_busy", group=group_name)
                    next_task = self._finish_task(wh.id)
                if next_task is None:
                    break
                task = next_task

            self._task_queue.task_done()

    def _finish_task(self, webhook_id: int | None) -> WebhookTask | None:
        if webhook_id is None:
            return None

        with self._dispatch_lock:
            if self._queued_task_count > 0:
                self._queued_task_count -= 1

            pending = self._pending_tasks.get(webhook_id)
            if not pending:
                self._active_webhooks.discard(webhook_id)
                self._pending_tasks.pop(webhook_id, None)
                return None

            next_task = pending.popleft()
            if not pending:
                self._pending_tasks.pop(webhook_id, None)
            return next_task

    @staticmethod
    def _replace_placeholders(template: str, group_name: str, message: str) -> str:
        # JSON 转义:json.dumps 生成带引号的安全字符串,[1:-1] 取内部转义后的内容
        replacements = {
            "{{group_name}}": json.dumps(group_name, ensure_ascii=False)[1:-1],
            "{{message}}": json.dumps(message, ensure_ascii=False)[1:-1],
        }
        for placeholder, value in replacements.items():
            template = template.replace(placeholder, value)
        return template

    @staticmethod
    def _parse_json_payload(raw: str, field_name: str, allowed_type: type | tuple[type, ...] | None = None) -> object:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning(f"Webhook {field_name} JSON 解析失败: {e}")
            return _INVALID_JSON
        if allowed_type is not None and not isinstance(data, allowed_type):
            logger.warning(f"Webhook {field_name} 解析类型非法: {type(data)}")
            return _INVALID_JSON
        return data

    @staticmethod
    def _coerce_headers(headers: object) -> dict[str, str] | None:
        if not isinstance(headers, dict):
            logger.warning("Webhook headers 不是 JSON 对象")
            return None
        safe_headers: dict[str, str] = {}
        for k, v in headers.items():
            if not isinstance(k, str) or not k.strip():
                logger.warning(f"Webhook headers 包含非法 header key: {k}")
                return None
            safe_headers[k] = str(v)
        return safe_headers

    @staticmethod
    def _clean_reply_text(value: str) -> str:
        cleaned = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", value.strip())
        return cleaned

    def _compile_jsonpath(self, webhook_id: int, path: str):
        cache_key = (webhook_id, path)
        with self._jsonpath_cache_lock:
            cached = self._jsonpath_cache.get(cache_key)
            if cached is not None:
                return cached
        expr = parse_jsonpath(path)
        with self._jsonpath_cache_lock:
            self._jsonpath_cache[cache_key] = expr
        return expr

    def _send(self, wh: Webhook, group_name: str, message: str):
        started = time.perf_counter()
        dispatch_result = "error"
        try:
            reply_mode = wh.reply_mode or ReplyMode.NONE.value
            request_timeout = (
                config.WEBHOOK_REPLY_TIMEOUT
                if reply_mode != ReplyMode.NONE.value
                else config.WEBHOOK_TIMEOUT
            )

            body_str = self._replace_placeholders(wh.body_template, group_name, message)
            body = self._parse_json_payload(body_str, f"body_template({wh.id})")
            if body is _INVALID_JSON:
                dispatch_result = "invalid_payload"
                return

            headers_str = self._replace_placeholders(wh.headers, group_name, message)
            headers = self._parse_json_payload(headers_str, f"headers({wh.id})", dict)
            if headers is _INVALID_JSON:
                dispatch_result = "invalid_payload"
                return
            headers = self._coerce_headers(headers)
            if headers is None:
                dispatch_result = "invalid_payload"
                return

            resp = self._http_client.post(wh.url, json=body, headers=headers, timeout=request_timeout)
            resp.raise_for_status()
            dispatch_result = "success"

            logger.debug(
                f"Webhook [{wh.name}] 发送成功: {resp.status_code} -> "
                f"{group_name}: {_preview_text(message, self._REPLY_PREVIEW_LEN)}"
            )

            if self._gateway_client:
                if reply_mode == ReplyMode.NONE.value:
                    logger.debug(f"Webhook [{wh.name}] reply_mode=none,跳过回复")
                    return

                if reply_mode not in {e.value for e in ReplyMode}:
                    logger.warning(f"Webhook [{wh.name}] reply_mode 非法值: {reply_mode},跳过回复")
                    return

                reply_text = None
                if reply_mode == ReplyMode.JSONPATH.value and wh.reply_jsonpath:
                    # JSONPath 模式
                    try:
                        data = resp.json()
                        path = wh.reply_jsonpath.strip()
                        matches = [match.value for match in self._compile_jsonpath(wh.id, path).find(data)]
                        if matches:
                            if len(matches) == 1:
                                reply_text = _stringify_jsonpath_value(matches[0])
                            else:
                                reply_text = json.dumps(matches, ensure_ascii=False)
                    except Exception as e:
                        logger.warning(f"Webhook [{wh.name}] JSONPath 提取失败: {e}")
                elif reply_mode == ReplyMode.TEXT.value:
                    # 纯文本模式
                    reply_text = self._clean_reply_text(resp.text)

                if not reply_text:
                    return

                if len(reply_text) > config.WEBHOOK_MAX_REPLY_TEXT:
                    reply_text = reply_text[:config.WEBHOOK_MAX_REPLY_TEXT]

                try:
                    self._gateway_client.send_message(OutgoingMessage(
                        group_name=group_name,
                        message=reply_text,
                    ))
                    logger.info(f"Webhook [{wh.name}] 回复到群聊: {group_name} => {_preview_text(reply_text, 80)}")
                except Exception as send_err:
                    logger.error(f"Webhook [{wh.name}] 回复发送失败: {send_err}")
        except httpx.TimeoutException as e:
            dispatch_result = "timeout"
            logger.warning(f"Webhook [{wh.name}] 请求超时: {e}")
        except httpx.HTTPStatusError as e:
            dispatch_result = "http_error"
            preview = _preview_text(e.response.text, self._REPLY_PREVIEW_LEN)
            logger.warning(f"Webhook [{wh.name}] HTTP 响应失败 {e.response.status_code}: {preview}")
        except httpx.RequestError as e:
            dispatch_result = "network_error"
            logger.warning(f"Webhook [{wh.name}] 网络请求失败: {e}")
        except Exception as e:
            logger.warning(f"Webhook [{wh.name}] 发送失败: {e}")
        finally:
            metrics.increment("webhook_dispatches_total", group=group_name, result=dispatch_result)
            metrics.observe(
                "webhook_dispatch_duration_seconds",
                time.perf_counter() - started,
                group=group_name,
                result=dispatch_result,
            )

    def close(self):
        self._running = False
        try:
            for _ in self._workers:
                try:
                    self._task_queue.put_nowait(_STOP_WEBHOOK_TASK)
                except queue.Full:
                    logger.warning("Webhook 队列已满,关闭信号入队失败,将等待线程按空队列超时退出")
            for worker in self._workers:
                worker.join(timeout=5)
            self._workers.clear()
        except Exception as e:
            logger.warning(f"关闭 webhook worker 失败: {e}")
        try:
            self._http_client.close()
        except Exception as e:
            logger.warning(f"关闭 HTTP 客户端失败: {e}")
        self.engine.dispose()
