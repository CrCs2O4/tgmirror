# Copy Forward Mode

**Date:** 2026-03-05

## Problem

Telegram's native `forward_messages()` API fails on sources that have forwarding restricted. There is currently no fallback — those messages are silently skipped.

## Solution

Add a per-source `mode` config field. When set to `"copy"`, tgmirror downloads the message content and re-sends it to the destination channel instead of using the native forward API.

## Configuration

Each source gets an optional `mode` field. Valid values: `"forward"` (default) and `"copy"`.

```toml
[[sources]]
id            = -1001234567890
name          = "Restricted Channel"
backfill_from = "2025-01-01"
mode          = "copy"

[[sources]]
id            = -1009999999999
name          = "Normal Group"
backfill_from = 0
mode          = "forward"
```

- `mode` is per-source only — no global default in `[settings]`
- Always written to config by the wizard (explicit is better than implicit)
- Invalid values log a warning and fall back to `"forward"`

## Core Copy Logic (`forwarder.py`)

A new `_copy_message()` async function handles the download-and-re-send path.

**Flow:**
1. Inspect `message.media` to determine type
2. Download the file to a temp location via `client.download_media(message)`
3. Re-send using the appropriate `client.send_*()` call with the downloaded file and `message.caption`
4. Clean up the temp file after send (success or failure)
5. On any exception, send a placeholder message instead

**Supported types:**

| Media type | Send method |
|---|---|
| `PHOTO` | `send_photo()` |
| `VIDEO` | `send_video()` |
| `DOCUMENT` | `send_document()` (includes PDFs, ZIPs, etc.) |
| `AUDIO` | `send_audio()` |
| `VOICE` | `send_voice()` |
| `VIDEO_NOTE` | `send_video_note()` |
| `STICKER` | `send_sticker()` |
| `ANIMATION` | `send_animation()` |
| `None` (text only) | `send_message()` |
| Anything else | placeholder |

Types with no downloadable file (`POLL`, `CONTACT`, `LOCATION`, `DICE`, `STORY`, etc.) fall through to the placeholder path.

**Placeholder format:**
```
[Could not forward message]
Original: t.me/c/<channel_id>/<message_id>
```

The `t.me/c/` link uses the bare channel ID (strips the `-100` prefix). This link works for private channels as long as the recipient is a member.

**Caption/text:** Preserved as-is. No attribution line added.

**FloodWait:** `_copy_message()` wraps `send_*()` the same way `_safe_forward()` wraps `forward_messages()` — catch `FloodWait`, sleep, retry once.

## Integration Into Forwarding Flow

A new `_dispatch()` function replaces direct `_safe_forward()` calls in both the backfill loop and the live handler:

```python
async def _dispatch(client, message, source, dest_id, state):
    mode = source.get("mode", "forward")
    if mode == "copy":
        await _copy_message(client, message, source, dest_id)
    else:
        await _safe_forward(client, dest_id, message.chat.id, message.id)
    state.set(message.chat.id, message.id)
```

- `state.set()` is called after both successful sends and placeholder sends
- No special handling needed for backfill vs live — `_dispatch()` works identically in both phases

## Setup Wizard Changes (`setup.py`)

After collecting `backfill_from` for each source, the wizard adds a new step:

```
── Forward mode ──────────────────────
How should messages from this source be forwarded?
  a) forward — native Telegram forward (default)
  b) copy    — download & re-send (use for sources that restrict forwarding)
Forward mode [a]:
```

- Uses the existing `ask_choice()` helper, default `"a"`
- `write_toml()` always writes the `mode` field for each source

## Tests

### `tests/test_forwarder.py` (new)

| Test | Coverage |
|---|---|
| `test_dispatch_uses_forward_for_default_mode` | `_dispatch()` routes to `_safe_forward()` when mode is `"forward"` or omitted |
| `test_dispatch_uses_copy_for_copy_mode` | `_dispatch()` routes to `_copy_message()` when mode is `"copy"` |
| `test_copy_message_photo` | Photo: downloads, calls `send_photo()`, cleans up temp file |
| `test_copy_message_document` | Document: downloads, calls `send_document()` with caption |
| `test_copy_message_text_only` | No media: calls `send_message()` with message text |
| `test_copy_message_unsupported_type` | Undownloadable type (e.g. POLL): sends placeholder |
| `test_copy_message_download_fails` | `download_media()` raises: sends placeholder with original link |
| `test_copy_message_send_fails` | `send_photo()` raises: sends placeholder with original link |
| `test_placeholder_link_format` | Placeholder contains correct `t.me/c/<bare_id>/<msg_id>` link |

### `tests/test_setup.py` (new)

| Test | Coverage |
|---|---|
| `test_validate_api_hash_valid` | 32-char hex passes |
| `test_validate_api_hash_invalid` | Wrong length or non-hex fails |
| `test_validate_phone_valid` | E.164 format passes |
| `test_validate_phone_invalid` | Missing `+` or too short fails |
| `test_validate_chat_id_valid` | Negative integer passes |
| `test_validate_chat_id_invalid` | Positive integer or non-integer fails |
