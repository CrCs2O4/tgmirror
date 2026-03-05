# Changelog

## 2026.03.05

Added a `mode = "copy"` option per source that downloads each message and re-uploads it instead of using Telegram's native forward. This is useful for sources that restrict forwarding. Supported media types are photos and documents; unsupported types fall back to a placeholder link.

Backfill was reworked to deliver messages to the destination in chronological (oldest-first) order. Previously messages arrived newest-first. The new implementation collects the full backfill window in one pass then dispatches in reverse, so the destination channel reads naturally. Resumability after Ctrl+C is preserved: state tracks the highest dispatched message ID and the next run skips everything up to that point.

Several backfill bugs were fixed along the way: state updates were moved out of `_dispatch` into the caller so interrupts are handled correctly; the `offset_date` argument was replaced with the two-phase approach to avoid a pyrofork pagination bug; and a `TypeError` when comparing timezone-aware date floors against pyrofork's naive `message.date` datetimes was resolved.

The setup wizard gained a `mode` prompt so new configs can opt into copy mode interactively. `config.example.toml` was updated to document the field.

A `make debug` target was added that sets `LOG_LEVEL=DEBUG` and suppresses pyrogram's own verbose debug output so only tgmirror's logs appear.

## 2026.03.04

Initial release. Mirrors one or more Telegram channels to a destination channel using native forwarding. Supports backfill from a configurable date or message ID, live monitoring via message handlers, per-source delay, and a setup wizard for first-time configuration.
