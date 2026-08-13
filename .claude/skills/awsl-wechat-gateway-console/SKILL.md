---
name: awsl-wechat-gateway-console
description: "Use when Codex needs to list or scroll chats, or send a message through the AWSL WeChat Gateway Windows main-window Console. Treat Console UI state and send results as non-authoritative; confirm every send through awsl-wechat-chatlog before reporting success or retrying. Reuses awsl-wechat-gateway connection and authentication."
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

# Scroll the virtualized chat list, then inspect the newly visible chats
uv run ".claude/skills/awsl-wechat-gateway-console/scripts/awsl_gateway_console.py" scroll down
uv run ".claude/skills/awsl-wechat-gateway-console/scripts/awsl_gateway_console.py" scroll up

# Open the chat and request a send
uv run ".claude/skills/awsl-wechat-gateway-console/scripts/awsl_gateway_console.py" send \
  --chat "Bruce" --message "Console test"

# Send attachments or Windows voice messages
uv run ".claude/skills/awsl-wechat-gateway-console/scripts/awsl_gateway_console.py" send \
  --chat "Bruce" --message-type image --file-path ./image.png
uv run ".claude/skills/awsl-wechat-gateway-console/scripts/awsl_gateway_console.py" send \
  --chat "Bruce" --message-type voice --message "Console TTS test"

```

`list` returns only the chats currently exposed by the virtualized WeChat list. Use `scroll up` or `scroll down` repeatedly until the target becomes visible.

`send` resolves the exact chat-list item and uses Console UIA state while opening and sending. UI recognition/validation and the actual send are decoupled: a validation failure or error response does not prove that no message was sent.

## Send Confirmation (Required)

Treat Chatlog as the only source of truth for whether a send occurred. For every `send` invocation, including one that exits with an error such as `Failed to open chat` or reports the current chat as unrecognized:

1. Resolve the destination to its stable Chatlog `username` with `awsl-wechat-chatlog groups`, preferably before sending.
2. Run the Console `send` command once.
3. Query the destination's newest records with `awsl-wechat-chatlog messages --group <username> --limit 20 --desc`.
4. Confirm a matching record with `is_self=true` in the send time window. Match the text exactly when possible; for attachments or voice, also use the message type, filename/content metadata, and timestamp available in Chatlog.
5. Report the send as successful when that record exists, regardless of the Console command's UI error or return status.
6. Do not immediately retry any failed or ambiguous Console send. Retry only after Chatlog confirms that no matching self-sent record exists; re-query first if ingestion timing is uncertain.

Never infer send success or failure from the Console UI, UIA title recognition, process exit code, or API response alone. Blindly retrying an `open chat failed`-style error can send a duplicate message.

## Chat History

Do not treat Console UIA bubbles as chat history or reliable evidence of whether a message was sent. Use `awsl-wechat-chatlog` for all history and send confirmation, as well as search, contacts, members, and media:

1. List Chatlog groups.
2. Match the Console chat name against `username`, `display_name`, `remark`, or `nick_name`.
3. Query messages using the matched stable `username`.
