---
name: awsl-wechat-gateway-console
description: "Use when Codex needs to list chats or send a message through the AWSL WeChat Gateway Windows main-window Console. Sending opens the named chat first, verifies that it opened, then sends with backend chat-name validation. Reuses awsl-wechat-gateway connection and authentication; use awsl-wechat-chatlog for chat history."
---

# AWSL WeChat Gateway Console

Run commands from the repository root with the bundled CLI:

```bash
uv run ".claude/skills/awsl-wechat-gateway-console/scripts/awsl_gateway_console.py" list
```

## Connection

Reuse `~/.config/awsl-wechat-gateway/config.json`. Do not create Console-specific configuration.
Configure or inspect the shared connection with the Gateway CLI:

```bash
uv run ".claude/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" config show
uv run ".claude/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" \
  config set --base-url http://127.0.0.1:8000 --token "$HTTP_API_TOKEN"
```

Explicit `--base-url` and `--token` overrides use the same Gateway cache. Add `--no-save` to avoid updating it.

## Commands

```bash
# List main-window chats
uv run ".claude/skills/awsl-wechat-gateway-console/scripts/awsl_gateway_console.py" list

# Open the chat, verify the opened title, then send
uv run ".claude/skills/awsl-wechat-gateway-console/scripts/awsl_gateway_console.py" send \
  --chat "Bruce" --message "Console test"

# Send attachments or Windows voice messages
uv run ".claude/skills/awsl-wechat-gateway-console/scripts/awsl_gateway_console.py" send \
  --chat "Bruce" --message-type image --file-path ./image.png
uv run ".claude/skills/awsl-wechat-gateway-console/scripts/awsl_gateway_console.py" send \
  --chat "Bruce" --message-type voice --message "Console TTS test"

```

`send` resolves the exact chat-list item, opens it, and checks that the refreshed current-chat title matches. It sends only after that check; the backend validates the live title again immediately before sending.

## Chat History

Do not treat Console UIA bubbles as chat history. Delegate history, search, contacts, members, and media to `awsl-wechat-chatlog`:

1. List Chatlog groups.
2. Match the Console chat name against `username`, `display_name`, `remark`, or `nick_name`.
3. Query messages using the matched stable `username`.
