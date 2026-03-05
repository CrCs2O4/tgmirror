# Telegram Group Forwarder — Design Document

## Summary

A Python user-client application that monitors one or more invite-only Telegram groups
and forwards all messages to a configured destination (channel or group). Runs as a
persistent daemon on a server, or locally on demand.

## Architecture

Uses **Pyrogram** (MTProto user-client library) — not the Bot API. This allows the
program to act as a real Telegram user account, giving it access to invite-only groups
the account is a member of.

Two phases run in sequence on startup:

1. **Backfill**: walks message history of each source group from a configured start
   point (date or message ID), forwarding all messages to the destination.
2. **Live**: registers real-time event handlers, forwarding new messages as they arrive.

Forwarding is **native** (`forward_messages`) — no download/re-upload. Telegram handles
it server-side, preserving original quality, captions, and metadata.

## Data Flow

```
startup
  └─► for each source group:
        └─► backfill from config backfill_from (date or message ID)
              └─► for each message → forward_messages() to destination
              └─► update state.json (last forwarded message ID)
  └─► register live message handlers for all source groups
  └─► run event loop forever

on new message in any source group:
  └─► forward_messages() to destination
  └─► update state.json
```

## File Structure

```
telegram-forwarder/
├── config.toml          # all configuration (API keys, source/dest IDs)
├── config.example.toml  # example config (committed to git)
├── main.py              # entry point: backfill → live
├── client.py            # Pyrogram session setup and auth
├── forwarder.py         # backfill logic + live event handlers
├── state.py             # read/write state.json (last message ID per source)
├── state.json           # persisted state (gitignored)
├── requirements.txt
└── .gitignore
```

## Config Schema (`config.toml`)

```toml
[telegram]
api_id = 12345
api_hash = "abc..."
phone = "+1234567890"

[settings]
backfill_delay_seconds = 0.5   # delay between forwarded messages during backfill

[[sources]]
id = -1001234567890
name = "My Source Group"       # human label, for logging only
backfill_from = "2025-01-01"   # ISO date string, or integer message ID

[[sources]]
id = -1009999999999
name = "Another Group"
backfill_from = 0              # 0 = from the very beginning

[destination]
id = -1008888888888
name = "My Channel"
```

## State Schema (`state.json`)

```json
{
  "-1001234567890": 12345,
  "-1009999999999": 67890
}
```

Stores the last forwarded message ID per source group. On restart, backfill resumes
from `max(state[source_id], backfill_from)` — preventing duplicates.

## Auth & Session

Pyrogram stores a `.session` file after first interactive login (phone + OTP + optional
2FA). Subsequent runs reuse it silently.

**Server deployment workflow:**
1. Auth locally: `python main.py` → enter phone/OTP
2. Copy `.session` file to server
3. Run as daemon on server

## Error Handling

| Error | Handling |
|---|---|
| `FloodWait` | Catch, sleep required duration, retry |
| Lost group access | Log warning, skip source, continue others |
| Network drop | Pyrogram reconnects automatically |
| Restart mid-backfill | `state.json` prevents re-forwarding |

## Dependencies

```
pyrogram==2.0.106
tgcrypto          # fast crypto for Pyrogram (optional but recommended)
tomllib           # built-in Python 3.11+, else tomli backport
```

## Non-Goals

- No web UI
- No download to disk (native forward only)
- No message filtering (all messages forwarded)
- No multi-account support
