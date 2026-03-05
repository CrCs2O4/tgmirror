import pytest
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from pyrogram.enums import MessageMediaType

from forwarder import _dispatch, _copy_message, _placeholder_link, backfill
from state import State


# ── helpers ───────────────────────────────────────────────────────────────────

_EPOCH = datetime(2026, 1, 1)  # naive, matching pyrofork's message.date


def make_message(
    media=None,
    text="hello",
    caption=None,
    chat_id=-1001234567890,
    msg_id=42,
    date=_EPOCH,
):
    msg = MagicMock()
    msg.media = media
    msg.text = text
    msg.caption = caption
    msg.chat.id = chat_id
    msg.id = msg_id
    msg.date = date
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
async def test_backfill_dispatches_oldest_first():
    """Messages are dispatched oldest-first (ascending ID) regardless of API order."""
    client = make_client()
    state = State(path=":memory:")
    source = {"id": -1001111111111, "mode": "forward", "backfill_from": 0}

    # API returns newest→oldest
    msgs = [make_message(chat_id=-1001111111111, msg_id=i) for i in (100, 99, 98)]
    client.get_chat_history = MagicMock(return_value=_async_messages(msgs))

    forwarded_ids = []

    async def capture(client_, dest_id, source_id, msg_id):
        forwarded_ids.append(msg_id)

    with patch("forwarder._safe_forward", side_effect=capture):
        await backfill(client, source, -1008888888888, state, delay=0)

    assert forwarded_ids == [98, 99, 100]


@pytest.mark.asyncio
async def test_backfill_state_is_highest_dispatched():
    """After a full run, state equals the highest (most recent) dispatched ID."""
    client = make_client()
    state = State(path=":memory:")
    source = {"id": -1001111111111, "mode": "forward", "backfill_from": 0}

    msgs = [make_message(chat_id=-1001111111111, msg_id=i) for i in (100, 99)]
    client.get_chat_history = MagicMock(return_value=_async_messages(msgs))

    with patch("forwarder._safe_forward", new_callable=AsyncMock):
        await backfill(client, source, -1008888888888, state, delay=0)

    assert state.get(-1001111111111) == 100


@pytest.mark.asyncio
async def test_backfill_interrupted_state_reflects_partial_progress():
    """If interrupted mid-dispatch, state holds the highest ID sent so far,
    so the next run resumes from where it left off without re-sending."""
    client = make_client()
    state = State(path=":memory:")
    source = {"id": -1001111111111, "mode": "forward", "backfill_from": 0}

    # API returns 100, 99, 98 (newest→oldest); dispatched oldest-first: 98, 99, 100.
    # Interrupt after dispatching 99 (second call).
    call_count = 0

    async def forward_twice_then_interrupt(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 3:
            raise KeyboardInterrupt

    msgs = [make_message(chat_id=-1001111111111, msg_id=i) for i in (100, 99, 98)]
    client.get_chat_history = MagicMock(return_value=_async_messages(msgs))

    with patch("forwarder._safe_forward", side_effect=forward_twice_then_interrupt):
        try:
            await backfill(client, source, -1008888888888, state, delay=0)
        except KeyboardInterrupt:
            pass

    # Dispatched 98 and 99; state should be 99 (highest sent)
    assert state.get(-1001111111111) == 99


@pytest.mark.asyncio
async def test_backfill_resumes_skipping_already_sent():
    """On resume, messages with ID <= last_id are skipped; only newer ones are sent."""
    client = make_client()
    state = State(path=":memory:")
    state.set(-1001111111111, 99)  # simulate: previously sent up to and including 99
    source = {"id": -1001111111111, "mode": "forward", "backfill_from": 0}

    # API returns all messages newest→oldest; 99 and below should be skipped
    msgs = [
        make_message(chat_id=-1001111111111, msg_id=i) for i in (102, 101, 100, 99, 98)
    ]
    client.get_chat_history = MagicMock(return_value=_async_messages(msgs))

    forwarded_ids = []

    async def capture(client_, dest_id, source_id, msg_id):
        forwarded_ids.append(msg_id)

    with patch("forwarder._safe_forward", side_effect=capture):
        await backfill(client, source, -1008888888888, state, delay=0)

    # Only 100, 101, 102 are new; dispatched oldest-first
    assert forwarded_ids == [100, 101, 102]


@pytest.mark.asyncio
async def test_backfill_date_floor_excludes_older_messages():
    """Messages whose date is before backfill_from are not dispatched."""
    client = make_client()
    state = State(path=":memory:")
    source = {"id": -1001111111111, "mode": "forward", "backfill_from": "2026-01-01"}

    msgs = [
        make_message(
            chat_id=-1001111111111,
            msg_id=102,
            date=datetime(2026, 3, 1),
        ),
        make_message(
            chat_id=-1001111111111,
            msg_id=101,
            date=datetime(2026, 1, 1),
        ),
        make_message(
            chat_id=-1001111111111,
            msg_id=100,
            date=datetime(2025, 12, 1),
        ),
        make_message(
            chat_id=-1001111111111,
            msg_id=99,
            date=datetime(2025, 6, 1),
        ),
    ]
    client.get_chat_history = MagicMock(return_value=_async_messages(msgs))

    forwarded_ids = []

    async def capture(client_, dest_id, source_id, msg_id):
        forwarded_ids.append(msg_id)

    with patch("forwarder._safe_forward", side_effect=capture):
        await backfill(client, source, -1008888888888, state, delay=0)

    # 101 (exactly on floor) and 102 (above) are included; 100 and 99 are before floor
    assert forwarded_ids == [101, 102]


@pytest.mark.asyncio
async def test_backfill_date_floor_works_with_naive_message_dates():
    """Backfill must not crash when pyrofork returns naive (no tzinfo) datetimes."""
    client = make_client()
    state = State(path=":memory:")
    source = {"id": -1001111111111, "mode": "forward", "backfill_from": "2026-01-01"}

    # Simulate pyrofork returning naive datetimes (no tzinfo)
    msgs = [
        make_message(
            chat_id=-1001111111111,
            msg_id=102,
            date=datetime(2026, 3, 1),  # naive
        ),
        make_message(
            chat_id=-1001111111111,
            msg_id=100,
            date=datetime(2025, 12, 1),  # naive, before floor
        ),
    ]
    client.get_chat_history = MagicMock(return_value=_async_messages(msgs))

    forwarded_ids = []

    async def capture(client_, dest_id, source_id, msg_id):
        forwarded_ids.append(msg_id)

    with patch("forwarder._safe_forward", side_effect=capture):
        await backfill(client, source, -1008888888888, state, delay=0)

    assert forwarded_ids == [102]


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
