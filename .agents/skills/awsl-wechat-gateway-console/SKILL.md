---
name: awsl-wechat-gateway-console
description: "Use when Codex needs to list or scroll chats, or send a message through the AWSL WeChat Gateway Windows/Linux main-window Console. Treat Console UI state and send results as non-authoritative; confirm every send through awsl-wechat-chatlog, and wait at least two minutes before declaring failure or retrying. Reuses awsl-wechat-gateway connection and authentication."
---

# AWSL WeChat Gateway Console

Run commands from the repository root with the bundled CLI:

```bash
uv run ".agents/skills/awsl-wechat-gateway-console/scripts/awsl_gateway_console.py" list
```

## Connection

Reuse `~/.config/awsl-wechat-gateway/config.json`. Do not create Console-specific configuration.
Configure or inspect the shared connection with the Gateway CLI:

```bash
uv run ".agents/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" config show
uv run ".agents/skills/awsl-wechat-gateway/scripts/awsl_gateway.py" \
  config set --base-url http://127.0.0.1:8000 --token "$HTTP_API_TOKEN"
```

Explicit `--base-url` and `--token` overrides use the same Gateway cache. Add `--no-save` to avoid updating it.

## Commands

```bash
# List main-window chats
uv run ".agents/skills/awsl-wechat-gateway-console/scripts/awsl_gateway_console.py" list

# Scroll the virtualized chat list, then inspect the newly visible chats
uv run ".agents/skills/awsl-wechat-gateway-console/scripts/awsl_gateway_console.py" scroll down
uv run ".agents/skills/awsl-wechat-gateway-console/scripts/awsl_gateway_console.py" scroll up

# Open the chat and request a send
uv run ".agents/skills/awsl-wechat-gateway-console/scripts/awsl_gateway_console.py" send \
  --chat "Bruce" --message "Console test"

# Native all-members mention (Windows/Linux, owner/admin candidate required)
uv run ".agents/skills/awsl-wechat-gateway-console/scripts/awsl_gateway_console.py" send \
  --chat "测试群" --message "请查看群公告" --mention-all

# Send attachments or Windows/Linux voice messages
uv run ".agents/skills/awsl-wechat-gateway-console/scripts/awsl_gateway_console.py" send \
  --chat "Bruce" --message-type image --file-path ./image.png
uv run ".agents/skills/awsl-wechat-gateway-console/scripts/awsl_gateway_console.py" send \
  --chat "Bruce" --message-type voice --message "Console TTS test"

```

`list` returns only the chats currently exposed by the virtualized WeChat list. `send --chat` resolves the target with a deterministic traversal: it first scrolls upward until two consecutive ordered visible-list snapshots are identical, then scans downward only. It reports the chat as absent only after two consecutive downward snapshots are identical. The traversal limit is only a safety guard; reaching it does not prove that either edge was reached.

When resolving a chat manually, follow the same rules:

1. Record the ordered visible chat signature, including each item's `path` and exact `name`.
2. Run `scroll up` repeatedly until two consecutive signatures are identical. This confirms the top regardless of whether the list started at the top, middle, or bottom.
3. Inspect the top snapshot, then use only `scroll down` while searching.
4. Stop when one exact-name match is visible. If multiple visible items have the same exact name, report ambiguity instead of choosing one.
5. Report not found only when two consecutive downward signatures are identical, confirming the bottom.

Never alternate scroll directions or treat the traversal limit as an edge. Windows `ChatSessionCell` AutomationIds use the display-name form `session_item_<name>`. Linux returns opaque session `path` values from AT-SPI; refresh state before using them. Neither exposes the stable WeChat chat ID. Resolve it through Chatlog and do not infer it from a path.

`send` resolves the exact chat-list item and uses Console UIA/AT-SPI state while opening and sending. UI recognition/validation and the actual send are decoupled: a validation failure or error response does not prove that no message was sent.

`--mention-all` sets `mention_all=true` for one text message. It types `@` and directly clicks the first “所有人” / “Mention All” candidate without searching. If the candidate is missing or disabled, sending stops; it never falls back to literal text. Writing `@所有人` / `@all` in the body does not enable it. Confirm native all-members delivery through Chatlog `at_users` containing `notify@all`, following the confirmation window below.

Linux requires WeChat started with `QT_LINUX_ACCESSIBILITY_ALWAYS_ON=1`, a shared X11 user/session, and Chatlog v2; voice also needs PulseAudio. Current-chat controls may temporarily disappear from AT-SPI, including zero-sized rectangles: stop on an unrecognized target and refresh or reselect the chat before sending. Native `@name ` prefixes require unique visible member candidates and fall back to literal text on ambiguity. See the repository README for setup and platform limits.

Linux WeChat detaches the current chat area when opening a separate window. Console reselects the chat after opening to restore the main window; after a manual detach, select the chat again. A top-level `available=true` alone does not mean `chat_area` or `input_box` is ready. Ambiguous chat names are rejected. Linux quotes verify copied text before selecting the quote menu and confirming the preview.

Linux Console holds its operation lock during TTS synthesis; chat switching returns busy during that operation. If recording completion cannot be confirmed, automatic UI actions pause and the silent microphone remains active until cancellation is confirmed. Do not delete `~/.cache/awsl-wechat-gateway/pending-recording.json` to bypass this guard; cancel recording in WeChat and retry. Window-cache restore returns unavailable when any entry fails, with per-entry results and failure counts.

Linux state includes structured Chinese/English quotes and timestamp rows. Debug `include_patterns=true` returns actual AT-SPI interfaces, attributes and supported actions/text without invoking actions. If microphone restoration or module removal fails after recording, the recovery marker remains and subsequent UI operations retry cleanup; do not resend solely because the API reported failure. Saving a window cache with unresolved conversation IDs fails and preserves the existing cache.

## Send Confirmation (Required)

Treat Chatlog as the only source of truth for whether a send occurred. For every `send` invocation, including one that exits with an error such as `Failed to open chat` or reports the current chat as unrecognized:

1. Resolve the destination to its stable Chatlog `username` with `awsl-wechat-chatlog groups`, preferably before sending.
2. Run the Console `send` command once.
3. Query the destination's newest records with `awsl-wechat-chatlog messages --group <username> --limit 20 --desc` and keep polling Chatlog during the confirmation window. Do not invoke `send` again while polling.
4. Confirm a matching record with `is_self=true` in the send time window. Match text exactly when possible. For attachments/voice, query all types (`--all-types` in the Chatlog CLI or `text_only=false` over HTTP), then match type, filename/content metadata, and timestamp. Confirm native mentions with `at_users`; confirm quotes with the reference message ID in appmsg type 57.
5. Report the send as successful as soon as that record exists, regardless of the Console command's UI error or return status.
6. Treat an absent record as pending, not failed, until at least 120 seconds have elapsed since the first `send` invocation. After the 120-second deadline, perform one final fresh Chatlog query.
7. Declare failure or retry only when that final query still contains no matching self-sent record. Never shorten the 120-second minimum because the Console returned early or reported an error.

Never infer send success or failure from the Console UI, UIA title recognition, process exit code, or API response alone. Chatlog ingestion and sending can be delayed; blindly retrying an `open chat failed`-style error before the full confirmation window can send a duplicate message.

## Chat History

Do not treat Console UIA bubbles as chat history or reliable evidence of whether a message was sent. Use `awsl-wechat-chatlog` for all history and send confirmation, as well as search, contacts, members, and media:

1. List Chatlog groups.
2. Match the Console chat name against `username`, `display_name`, `remark`, or `nick_name`.
3. Query messages using the matched stable `username`.
