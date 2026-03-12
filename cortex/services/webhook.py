"""
Webhook 服务
收到新消息后向配置的 URL 发送 POST 请求
支持群聊名称过滤和消息正则过滤
"""

import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Optional

import httpx
from pydantic import BaseModel
from sqlmodel import SQLModel, Field, Session, create_engine, select

from common.models import WSMessageBroadcast
from cortex.models import MessageListener

logger = logging.getLogger(__name__)


def _now_local() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ==================== Models ====================

class WebhookBase(SQLModel):
    name: str
    url: str
    headers: str = "{}"
    body_template: str = "{}"
    group_filter: str = ""
    message_filter: str = ""
    enabled: bool = True


class Webhook(WebhookBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    created_at: Optional[str] = Field(default_factory=_now_local)
    updated_at: Optional[str] = Field(default_factory=_now_local)


class WebhookCreate(WebhookBase):
    pass


class WebhookUpdate(SQLModel):
    name: Optional[str] = None
    url: Optional[str] = None
    headers: Optional[str] = None
    body_template: Optional[str] = None
    group_filter: Optional[str] = None
    message_filter: Optional[str] = None
    enabled: Optional[bool] = None


class WebhookResponse(BaseModel):
    id: int
    name: str
    url: str
    headers: str
    body_template: str
    group_filter: str
    message_filter: str
    enabled: bool
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


# ==================== Service ====================

class WebhookService(MessageListener):
    """Webhook 服务 — SQLModel ORM + fire-and-forget 发送"""

    _FIRE_TIMEOUT = 10

    def __init__(self, db_path: str):
        self.engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False},
            echo=False,
        )
        Webhook.__table__.create(self.engine, checkfirst=True)
        self._pool = ThreadPoolExecutor(max_workers=3)
        self._http_client = httpx.Client(timeout=self._FIRE_TIMEOUT)
        self._cache: list[Webhook] = []
        self._cache_ts: float = 0
        self._cache_ttl: float = 5.0  # 缓存 5 秒
        self._cache_lock = threading.Lock()
        logger.info("Webhook 数据库初始化完成")

    # ==================== CRUD ====================

    def create(self, data: WebhookCreate) -> Webhook:
        with Session(self.engine) as session:
            webhook = Webhook.model_validate(data)
            session.add(webhook)
            session.commit()
            session.refresh(webhook)
            self._invalidate_cache()
            return webhook

    def get(self, webhook_id: int) -> Optional[Webhook]:
        with Session(self.engine) as session:
            return session.get(Webhook, webhook_id)

    def get_all(self) -> list[Webhook]:
        with Session(self.engine) as session:
            return list(session.exec(select(Webhook).order_by(Webhook.id.desc())).all())

    def update(self, webhook_id: int, data: WebhookUpdate) -> Optional[Webhook]:
        with Session(self.engine) as session:
            webhook = session.get(Webhook, webhook_id)
            if not webhook:
                return None
            update_data = data.model_dump(exclude_unset=True)
            webhook.sqlmodel_update(update_data)
            webhook.updated_at = _now_local()
            session.add(webhook)
            session.commit()
            session.refresh(webhook)
            self._invalidate_cache()
            return webhook

    def delete(self, webhook_id: int) -> bool:
        with Session(self.engine) as session:
            webhook = session.get(Webhook, webhook_id)
            if not webhook:
                return False
            session.delete(webhook)
            session.commit()
            self._invalidate_cache()
            return True

    # ==================== 触发 ====================

    def _invalidate_cache(self):
        with self._cache_lock:
            self._cache_ts = 0

    def _get_cached_all(self) -> list[Webhook]:
        with self._cache_lock:
            if time.time() - self._cache_ts < self._cache_ttl:
                return self._cache
        webhooks = self.get_all()
        with self._cache_lock:
            self._cache = webhooks
            self._cache_ts = time.time()
        return webhooks

    _MAX_REGEX_LEN = 200

    def _safe_regex_match(self, pattern: str, text: str) -> bool | None:
        """安全正则匹配，返回 None 表示正则无效"""
        if len(pattern) > self._MAX_REGEX_LEN:
            return None
        try:
            return bool(re.search(pattern, text))
        except re.error:
            return None

    def _handle_message(self, msg: WSMessageBroadcast) -> None:
        self.fire(msg.group_name, msg.content)

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
            self._pool.submit(self._send, wh, group_name, message)

    def test(self, wh: Webhook):
        """用固定示例数据测试单个 webhook"""
        self._pool.submit(self._send, wh, "测试群聊", "这是一条测试消息")

    def _replace_placeholders(self, template: str, group_name: str, message: str) -> str:
        # JSON 转义：确保替换值不会破坏 JSON 结构
        safe_group = json.dumps(group_name)[1:-1]  # 去掉外层引号
        safe_message = json.dumps(message)[1:-1]
        return template.replace("{{group_name}}", safe_group).replace("{{message}}", safe_message)

    def _send(self, wh: Webhook, group_name: str, message: str):
        try:
            body_str = self._replace_placeholders(wh.body_template, group_name, message)
            body = json.loads(body_str)

            headers_str = self._replace_placeholders(wh.headers, group_name, message)
            headers = json.loads(headers_str)

            resp = self._http_client.post(wh.url, json=body, headers=headers)
            logger.debug(f"Webhook [{wh.name}] 发送成功: {resp.status_code} → {group_name}: {message[:50]}...")
        except Exception as e:
            logger.warning(f"Webhook [{wh.name}] 发送失败: {e}")

    def close(self):
        self._pool.shutdown(wait=False)
        self._http_client.close()
        self.engine.dispose()
