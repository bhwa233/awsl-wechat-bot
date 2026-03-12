"""AWSL Gateway + NanoBot Agent - WS 客户端，收到消息调 NanoBot Agent 处理"""

import asyncio
import json
import logging

import websockets
from pydantic_settings import BaseSettings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
logger = logging.getLogger("awsl-nanobot")

class Settings(BaseSettings):
    gateway_ws_url: str = "ws://127.0.0.1:8000/ws"
    http_api_token: str = ""
    group_filter: str = ""
    message_filter: str = ""

    nanobot_model: str = "gpt-5.2-codex"
    nanobot_provider: str = "openai"
    nanobot_max_tokens: int = 4096
    nanobot_workspace: str = "~/.nanobot/workspace"

    llm_api_key: str = ""
    llm_base_url: str | None = None

    model_config = {"env_file": ".env", "extra": "ignore"}


settings = Settings()


def build_agent():
    from pathlib import Path

    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus
    from nanobot.providers.litellm_provider import LiteLLMProvider

    if not settings.llm_api_key:
        raise SystemExit("未配置 LLM_API_KEY，请检查 .env")

    workspace = Path(settings.nanobot_workspace).expanduser()
    workspace.mkdir(parents=True, exist_ok=True)

    return AgentLoop(
        bus=MessageBus(),
        provider=LiteLLMProvider(
            api_key=settings.llm_api_key,
            api_base=settings.llm_base_url,
            default_model=settings.nanobot_model,
            provider_name=settings.nanobot_provider,
        ),
        workspace=workspace,
        model=settings.nanobot_model,
        max_tokens=settings.nanobot_max_tokens,
    )


async def ws_handshake(ws) -> bool:
    if settings.http_api_token:
        await ws.send(json.dumps({"type": "auth", "token": settings.http_api_token}))
        resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        if resp.get("type") != "auth_ok":
            logger.error("认证失败: %s", resp)
            return False

    await ws.send(json.dumps({
        "type": "subscribe",
        "group_filter": settings.group_filter,
        "message_filter": settings.message_filter or "^(?![a-zA-Z]+\\s)(?!@\\S+\\s)",
        "priority": 20,
    }))
    resp = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
    if resp.get("type") != "subscribe_ok":
        logger.error("订阅失败: %s", resp)
        return False

    return True


async def handle_message(ws, agent, data: dict):
    group_name = data["group_name"]
    content = data["content"]
    logger.info("[%s] 收到: %s", group_name, content[:80])

    reply = await agent.process_direct(
        content,
        session_key=f"wechat:{group_name}",
        channel="wechat",
        chat_id=group_name,
    )
    if reply:
        await ws.send(json.dumps({
            "type": "send_message",
            "group_name": group_name,
            "message": reply,
        }))
        logger.info("[%s] 回复: %s", group_name, reply[:80])


async def run(agent):
    async for ws in websockets.connect(settings.gateway_ws_url):
        try:
            if not await ws_handshake(ws):
                continue
            logger.info("已连接 Gateway 并订阅")
            async for raw in ws:
                data = json.loads(raw)
                if data.get("type") == "message":
                    try:
                        await handle_message(ws, agent, data)
                    except Exception as e:
                        logger.error("[%s] 处理失败: %s", data.get("group_name"), e)
        except websockets.ConnectionClosed as e:
            logger.warning("连接断开: %s, 自动重连...", e)


def main():
    logger.info("NanoBot Agent 启动 (model=%s)", settings.nanobot_model)
    agent = build_agent()
    logger.info("连接 Gateway: %s", settings.gateway_ws_url)
    try:
        asyncio.run(run(agent))
    except KeyboardInterrupt:
        logger.info("已停止")


if __name__ == "__main__":
    main()
