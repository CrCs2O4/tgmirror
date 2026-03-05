import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pyrogram.enums import MessageMediaType

from forwarder import _dispatch, _copy_message, _placeholder_link, backfill
from state import State


# ── helpers ───────────────────────────────────────────────────────────────────


def make_message(
    media=None, text="hello", caption=None, chat_id=-1001234567890, msg_id=42
):
    msg = MagicMock()
    msg.media = media
    msg.text = text
    msg.caption = caption
    msg.chat.id = chat_id
    msg.id = msg_id
    return msg


def make_client():
    client = MagicMock()
    client.forward_messages = AsyncMock()
    client.download_media = AsyncMock(
        return_value="/fake/tmpdir/photo_2025-01-01_001.jpg"
    )
    client.send_message = AsyncMock()
    client.send_photo = AsyncMock()
    client.send_video = AsyncMock()
    client.send_document = AsyncMock()
    client.send_audio = AsyncMock()
    client.send_voice = AsyncMock()
    client.send_video_note = AsyncMock()
    client.send_sticker = AsyncMock()
    client.send_animation = AsyncMock()
    return client


# ── _placeholder_link ─────────────────────────────────────────────────────────


def test_placeholder_link_format():
    link = _placeholder_link(-1001234567890, 99)
    assert link == "t.me/c/1234567890/99"


def test_placeholder_link_strips_minus_100():
    link = _placeholder_link(-1009999999999, 1)
    assert link == "t.me/c/9999999999/1"


# ── _dispatch ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dispatch_forward_mode_calls_safe_forward():
    client = make_client()
    message = make_message()
    source = {"id": -1001234567890, "mode": "forward"}

    with patch("forwarder._safe_forward", new_callable=AsyncMock) as mock_fwd:
        await _dispatch(client, message, source, -1008888888888)
        mock_fwd.assert_called_once_with(
            client, -1008888888888, message.chat.id, message.id
        )


@pytest.mark.asyncio
async def test_dispatch_default_mode_calls_safe_forward():
    """mode omitted defaults to 'forward'."""
    client = make_client()
    message = make_message()
    source = {"id": -1001234567890}  # no mode key

    with patch("forwarder._safe_forward", new_callable=AsyncMock) as mock_fwd:
        await _dispatch(client, message, source, -1008888888888)
        mock_fwd.assert_called_once()


@pytest.mark.asyncio
async def test_dispatch_copy_mode_calls_copy_message():
    client = make_client()
    message = make_message()
    source = {"id": -1001234567890, "mode": "copy"}

    with patch("forwarder._copy_message", new_callable=AsyncMock) as mock_copy:
        await _dispatch(client, message, source, -1008888888888)
        mock_copy.assert_called_once_with(client, message, -1008888888888)


@pytest.mark.asyncio
async def test_dispatch_invalid_mode_falls_back_to_forward():
    client = make_client()
    message = make_message()
    source = {"id": -1001234567890, "mode": "bogus"}

    with patch("forwarder._safe_forward", new_callable=AsyncMock) as mock_fwd:
        await _dispatch(client, message, source, -1008888888888)
        mock_fwd.assert_called_once()


# ── _copy_message ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_copy_message_text_only():
    client = make_client()
    message = make_message(media=None, text="some text")

    await _copy_message(client, message, -1008888888888)

    client.send_message.assert_called_once_with(-1008888888888, "some text")
    client.download_media.assert_not_called()


@pytest.mark.asyncio
async def test_copy_message_photo():
    client = make_client()
    message = make_message(media=MessageMediaType.PHOTO, caption="a photo")

    with (
        patch("tempfile.mkdtemp", return_value="/fake/tmpdir"),
        patch("forwarder.os.path.exists", return_value=True),
        patch("os.remove"),
        patch("os.rmdir"),
    ):
        await _copy_message(client, message, -1008888888888)

    client.download_media.assert_called_once_with(message, file_name="/fake/tmpdir/")
    client.send_photo.assert_called_once_with(
        -1008888888888, "/fake/tmpdir/photo_2025-01-01_001.jpg", caption="a photo"
    )


@pytest.mark.asyncio
async def test_copy_message_document():
    client = make_client()
    message = make_message(media=MessageMediaType.DOCUMENT, caption="a pdf")

    with (
        patch("tempfile.mkdtemp", return_value="/fake/tmpdir"),
        patch("forwarder.os.path.exists", return_value=True),
        patch("os.remove"),
        patch("os.rmdir"),
    ):
        await _copy_message(client, message, -1008888888888)

    client.send_document.assert_called_once_with(
        -1008888888888, "/fake/tmpdir/photo_2025-01-01_001.jpg", caption="a pdf"
    )


@pytest.mark.asyncio
async def test_copy_message_unsupported_type_sends_placeholder():
    """Types with no downloadable file (POLL etc.) send a placeholder."""
    client = make_client()
    message = make_message(
        media=MessageMediaType.POLL, chat_id=-1001234567890, msg_id=5
    )

    await _copy_message(client, message, -1008888888888)

    client.download_media.assert_not_called()
    client.send_message.assert_called_once()
    call_text = client.send_message.call_args[0][1]
    assert "Could not forward" in call_text
    assert "t.me/c/" in call_text


@pytest.mark.asyncio
async def test_copy_message_download_fails_sends_placeholder():
    client = make_client()
    client.download_media = AsyncMock(side_effect=Exception("network error"))
    message = make_message(
        media=MessageMediaType.PHOTO, chat_id=-1001234567890, msg_id=10
    )

    with (
        patch("tempfile.mkdtemp", return_value="/fake/tmpdir"),
        patch("os.path.exists", return_value=False),
        patch("os.rmdir"),
    ):
        await _copy_message(client, message, -1008888888888)

    client.send_message.assert_called_once()
    call_text = client.send_message.call_args[0][1]
    assert "Could not forward" in call_text


@pytest.mark.asyncio
async def test_copy_message_send_fails_sends_placeholder():
    client = make_client()
    client.send_photo = AsyncMock(side_effect=Exception("send error"))
    message = make_message(
        media=MessageMediaType.PHOTO, chat_id=-1001234567890, msg_id=11
    )

    with (
        patch("tempfile.mkdtemp", return_value="/fake/tmpdir"),
        patch("forwarder.os.path.exists", return_value=True),
        patch("os.remove"),
        patch("os.rmdir"),
    ):
        await _copy_message(client, message, -1008888888888)

    client.send_message.assert_called_once()
    call_text = client.send_message.call_args[0][1]
    assert "Could not forward" in call_text


@pytest.mark.asyncio
async def test_copy_message_cleans_up_temp_file_and_dir():
    client = make_client()
    message = make_message(media=MessageMediaType.VIDEO)

    with (
        patch("tempfile.mkdtemp", return_value="/fake/tmpdir"),
        patch("forwarder.os.path.exists", return_value=True),
        patch("os.remove") as mock_rm,
        patch("os.rmdir") as mock_rd,
    ):
        await _copy_message(client, message, -1008888888888)

    mock_rm.assert_called_once_with("/fake/tmpdir/photo_2025-01-01_001.jpg")
    mock_rd.assert_called_once_with("/fake/tmpdir")


# ── backfill state resumability ───────────────────────────────────────────────


async def _async_messages(messages):
    """Yield a list of mock messages as an async generator (newest-first)."""
    for m in messages:
        yield m


@pytest.mark.asyncio
async def test_backfill_state_updates_per_message_newest_first():
    """After processing msg 100 then 99, state should be 99 (the last processed), not 100."""
    client = make_client()
    state = State(path=":memory:")
    source = {"id": -1001111111111, "mode": "forward", "backfill_from": 0}

    msgs = [make_message(chat_id=-1001111111111, msg_id=i) for i in (100, 99)]
    client.get_chat_history = MagicMock(return_value=_async_messages(msgs))

    with patch("forwarder._safe_forward", new_callable=AsyncMock):
        await backfill(client, source, -1008888888888, state, delay=0)

    assert state.get(-1001111111111) == 99


@pytest.mark.asyncio
async def test_backfill_interrupted_state_reflects_partial_progress():
    """If interrupted after first message (msg 100), state should be 100 so next run
    resumes from 99 downward — not from the beginning."""
    client = make_client()
    state = State(path=":memory:")
    source = {"id": -1001111111111, "mode": "forward", "backfill_from": 0}

    # Simulate Ctrl+C after the first message by raising on second forward call
    call_count = 0

    async def forward_once_then_interrupt(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            raise KeyboardInterrupt

    msgs = [make_message(chat_id=-1001111111111, msg_id=i) for i in (100, 99, 98)]
    client.get_chat_history = MagicMock(return_value=_async_messages(msgs))

    with patch("forwarder._safe_forward", side_effect=forward_once_then_interrupt):
        try:
            await backfill(client, source, -1008888888888, state, delay=0)
        except KeyboardInterrupt:
            pass

    # state should be 100 (msg 100 was processed), not 0 and not higher
    assert state.get(-1001111111111) == 100


@pytest.mark.asyncio
async def test_backfill_resumes_from_saved_state():
    """On second run, backfill should skip messages at or below the saved state ID."""
    client = make_client()
    state = State(path=":memory:")
    state.set(-1001111111111, 98)  # simulate: processed down to 98 last run
    source = {"id": -1001111111111, "mode": "forward", "backfill_from": 0}

    msgs = [make_message(chat_id=-1001111111111, msg_id=i) for i in (100, 99, 98, 97)]
    client.get_chat_history = MagicMock(return_value=_async_messages(msgs))

    forwarded_ids = []

    async def capture_forward(client_, dest_id, source_id, msg_id):
        forwarded_ids.append(msg_id)

    with patch("forwarder._safe_forward", side_effect=capture_forward):
        await backfill(client, source, -1008888888888, state, delay=0)

    # 100 and 99 were not yet processed (above state=98), so they should be forwarded
    # 98 and 97 were already processed (at or below state=98), so they should be skipped
    assert forwarded_ids == [100, 99]


@pytest.mark.asyncio
async def test_dispatch_does_not_update_state():
    """_dispatch should NOT update state — state management belongs to callers."""
    client = make_client()
    message = make_message(chat_id=-1001111111111, msg_id=42)
    state = State(path=":memory:")
    source = {"id": -1001111111111, "mode": "forward"}

    with patch("forwarder._safe_forward", new_callable=AsyncMock):
        await _dispatch(client, message, source, -1008888888888)

    assert state.get(-1001111111111) == 0  # state untouched
