import asyncio
import logging
import os
import tempfile
from datetime import datetime, timezone

from pyrogram import Client, filters as f
from pyrogram.enums import MessageMediaType
from pyrogram.errors import FloodWait

from state import State

logger = logging.getLogger(__name__)

_NO_DOWNLOAD_TYPES = {
    MessageMediaType.CONTACT,
    MessageMediaType.LOCATION,
    MessageMediaType.VENUE,
    MessageMediaType.POLL,
    MessageMediaType.DICE,
    MessageMediaType.GAME,
    MessageMediaType.GIVEAWAY,
    MessageMediaType.GIVEAWAY_RESULT,
    MessageMediaType.STORY,
    MessageMediaType.INVOICE,
    MessageMediaType.PAID_MEDIA,
    MessageMediaType.TODO,
    MessageMediaType.WEB_PAGE_PREVIEW,
}


def _placeholder_link(source_id: int, msg_id: int) -> str:
    """Build a t.me/c/ link. Works for private channels if recipient is a member."""
    bare_id = str(source_id).lstrip("-").removeprefix("100")
    return f"t.me/c/{bare_id}/{msg_id}"


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


async def _copy_message(client: Client, message, source: dict, dest_id: int):
    """Download and re-send a message, bypassing forward restrictions."""
    source_id = message.chat.id
    msg_id = message.id
    caption = message.caption or message.text or ""

    media_type = message.media

    if media_type in _NO_DOWNLOAD_TYPES:
        await _send_placeholder(client, dest_id, source_id, msg_id)
        return

    tmp_dir = None
    tmp_path = None
    try:
        if media_type is None:
            # Text-only message
            await client.send_message(dest_id, caption)
            return

        tmp_dir = tempfile.mkdtemp()
        tmp_path = await client.download_media(
            message, file_name=os.path.join(tmp_dir, "media")
        )

        if media_type == MessageMediaType.PHOTO:
            await _send_with_floodwait(
                client.send_photo, dest_id, tmp_path, caption=caption
            )
        elif media_type == MessageMediaType.VIDEO:
            await _send_with_floodwait(
                client.send_video, dest_id, tmp_path, caption=caption
            )
        elif media_type == MessageMediaType.DOCUMENT:
            await _send_with_floodwait(
                client.send_document, dest_id, tmp_path, caption=caption
            )
        elif media_type == MessageMediaType.AUDIO:
            await _send_with_floodwait(
                client.send_audio, dest_id, tmp_path, caption=caption
            )
        elif media_type == MessageMediaType.VOICE:
            await _send_with_floodwait(client.send_voice, dest_id, tmp_path)
        elif media_type == MessageMediaType.VIDEO_NOTE:
            await _send_with_floodwait(client.send_video_note, dest_id, tmp_path)
        elif media_type == MessageMediaType.STICKER:
            await _send_with_floodwait(client.send_sticker, dest_id, tmp_path)
        elif media_type == MessageMediaType.ANIMATION:
            await _send_with_floodwait(
                client.send_animation, dest_id, tmp_path, caption=caption
            )
        else:
            # Unknown downloadable type — try as document
            await _send_with_floodwait(
                client.send_document, dest_id, tmp_path, caption=caption
            )

    except Exception as e:
        logger.warning("Could not copy message %d from %d: %s", msg_id, source_id, e)
        await _send_placeholder(client, dest_id, source_id, msg_id)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)
        if tmp_dir and os.path.exists(tmp_dir):
            os.rmdir(tmp_dir)


async def _dispatch(
    client: Client,
    message,
    source: dict,
    dest_id: int,
    state: State,
):
    """Route a message to the correct forward strategy based on source mode."""
    mode = source.get("mode", "forward")
    if mode not in ("forward", "copy"):
        logger.warning(
            "Unknown mode %r for source %d — falling back to 'forward'",
            mode,
            source.get("id", "?"),
        )
        mode = "forward"

    if mode == "copy":
        await _copy_message(client, message, source, dest_id)
    else:
        await _safe_forward(client, dest_id, message.chat.id, message.id)

    if message.id > state.get(message.chat.id):
        state.set(message.chat.id, message.id)


async def _send_with_floodwait(send_fn, *args, **kwargs):
    """Call a send_* function, retrying once on FloodWait."""
    try:
        await send_fn(*args, **kwargs)
    except FloodWait as e:
        logger.warning("FloodWait: sleeping %ds", e.value)
        await asyncio.sleep(e.value + 1)
        await send_fn(*args, **kwargs)


async def _send_placeholder(client: Client, dest_id: int, source_id: int, msg_id: int):
    """Send a placeholder when a message cannot be copied."""
    link = _placeholder_link(source_id, msg_id)
    text = f"[Could not forward message]\nOriginal: {link}"
    await client.send_message(dest_id, text)


async def backfill(
    client: Client,
    source: dict,
    dest_id: int,
    state: State,
    delay: float,
):
    """Forward all messages from source to dest_id."""
    source_id = source["id"]
    backfill_from = source.get("backfill_from", 0)
    last_id = state.get(source_id)
    offset_date = _resolve_offset_date(backfill_from)
    min_msg_id = max(last_id, _resolve_min_message_id(backfill_from))

    logger.info(
        "Backfilling source %d from %s (last_id=%d)", source_id, backfill_from, last_id
    )

    async for message in client.get_chat_history(source_id, offset_date=offset_date):
        if message.id <= min_msg_id:
            break
        await _dispatch(client, message, source, dest_id, state)
        logger.debug("Forwarded message %d from %d", message.id, source_id)
        await asyncio.sleep(delay)

    logger.info("Backfill complete for source %d", source_id)


def register_live_handlers(
    client: Client, sources: list[dict], dest_id: int, state: State
):
    """Register a message handler that forwards new messages in real time."""
    source_ids = [s["id"] for s in sources]
    sources_by_id = {s["id"]: s for s in sources}

    @client.on_message(f.chat(source_ids))
    async def handler(c: Client, message):
        try:
            source = sources_by_id.get(message.chat.id, {"id": message.chat.id})
            await _dispatch(c, message, source, dest_id, state)
            logger.debug(
                "Live-forwarded message %d from %d", message.id, message.chat.id
            )
        except Exception as e:
            logger.error("Failed to forward message %d: %s", message.id, e)
