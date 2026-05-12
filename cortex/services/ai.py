"""AI 服务 - OpenAI API"""

import logging

import httpx

from common.config import config

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = "你是一个友好、有趣的 AI 助手,用简洁、幽默的方式回答问题."


class AIService:
    def __init__(self):
        self._client = httpx.Client(
            base_url=config.OPENAI_BASE_URL,
            headers={
                "Authorization": f"Bearer {config.OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(connect=10.0, read=30.0, write=10.0, pool=10.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        logger.info(f"AI 服务初始化完成,API: {config.OPENAI_BASE_URL}")

    def ask(self, question: str, system_prompt: str | None = None) -> str | None:
        try:
            resp = self._client.post("/chat/completions", json={
                "model": config.OPENAI_MODEL,
                "messages": [
                    {"role": "system", "content": system_prompt or _DEFAULT_SYSTEM_PROMPT},
                    {"role": "user", "content": question},
                ],
                "max_tokens": config.OPENAI_MAX_TOKENS,
                "temperature": config.OPENAI_TEMPERATURE,
            })
            resp.raise_for_status()
            answer = resp.json()["choices"][0]["message"]["content"].strip()
            logger.info(f"AI 回复: {answer[:80]}...")
            return answer
        except httpx.HTTPStatusError as e:
            logger.error(f"AI 请求失败 {e.response.status_code}: {e.response.text[:200]}")
            return None
        except Exception as e:
            logger.error(f"AI 请求异常: {e}")
            return None

    def close(self):
        self._client.close()
