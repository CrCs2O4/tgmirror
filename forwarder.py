import asyncio
import logging
from datetime import datetime, timezone

from pyrogram import Client, filters as f
from pyrogram.errors import FloodWait

from state import State

logger = logging.getLogger(__name__)


def _resolve_offset_date(backfill_from) -> datetime | None:
    """Convert backfill_from config value to a datetime or None."""
    if backfill_from == 0:
        return None
    if isinstance(backfill_from, str):
        dt = datetime.fromisoformat(backfill_from)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    return None  # treat integer as message ID (handled separately)


def _resolve_min_message_id(backfill_from) -> int:
    """Return minimum message ID from backfill_from if it's an integer."""
    if isinstance(backfill_from, int) and backfill_from > 0:
        return backfill_from
    return 0


async def _safe_forward(client: Client, dest_id: int, source_id: int, msg_id: int):
    """Forward a single message, retrying once on FloodWait."""
    try:
        await client.forward_messages(dest_id, source_id, msg_id)
    except FloodWait as e:
        logger.warning("FloodWait: sleeping %ds", e.value)
        await asyncio.sleep(e.value + 1)
        await client.forward_messages(dest_id, source_id, msg_id)


async def backfill(
    client: Client,
    source_id: int,
    backfill_from,
    dest_id: int,
    state: State,
    delay: float,
):
    """Forward all messages from source_id to dest_id starting from backfill_from."""
    last_id = state.get(source_id)
    offset_date = _resolve_offset_date(backfill_from)
    min_msg_id = max(last_id, _resolve_min_message_id(backfill_from))

    logger.info(
        "Backfilling source %d from %s (last_id=%d)", source_id, backfill_from, last_id
    )

    async for message in client.get_chat_history(source_id, offset_date=offset_date):
        if message.id <= min_msg_id:
            break
        await _safe_forward(client, dest_id, source_id, message.id)
        if message.id > state.get(source_id):
            state.set(source_id, message.id)
        logger.debug("Forwarded message %d from %d", message.id, source_id)
        await asyncio.sleep(delay)

    logger.info("Backfill complete for source %d", source_id)


def register_live_handlers(
    client: Client, source_ids: list[int], dest_id: int, state: State
):
    """Register a message handler that forwards new messages in real time."""

    @client.on_message(f.chat(source_ids))
    async def handler(c: Client, message):
        try:
            await _safe_forward(c, dest_id, message.chat.id, message.id)
            state.set(message.chat.id, message.id)
            logger.debug(
                "Live-forwarded message %d from %d", message.id, message.chat.id
            )
        except Exception as e:
            logger.error("Failed to forward message %d: %s", message.id, e)
