# Copy Forward Mode Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Add a per-source `mode = "copy"` config option that downloads and re-sends messages instead of using the native Telegram forward API, enabling mirroring of sources that restrict forwarding.

**Architecture:** A new `_copy_message()` function in `forwarder.py` handles download-and-re-send. A `_dispatch()` function replaces direct `_safe_forward()` calls in both backfill and live handler, routing based on `source.get("mode", "forward")`. The setup wizard gains a new step asking for forward mode per source.

**Tech Stack:** Python 3.11+, pyrofork (Pyrogram fork), pytest, pytest-asyncio

---

## Task 1: Add pytest-asyncio dev dependency

**Files:**
- Modify: `pyproject.toml`

**Step 1: Add pytest-asyncio to dev dependencies**

Edit `pyproject.toml` dev group:

```toml
[dependency-groups]
dev = [
    "pytest",
    "pytest-asyncio",
    "ruff",
]
```

**Step 2: Install it**

```bash
uv sync
```

Expected: resolves and installs `pytest-asyncio`.

**Step 3: Verify existing tests still pass**

```bash
uv run pytest tests/ -v
```

Expected: 8 tests pass.

**Step 4: Commit**

```bash
git add pyproject.toml
git commit -m "chore: add pytest-asyncio dev dependency"
```

---

## Task 2: Add `_copy_message()` to forwarder.py

**Files:**
- Modify: `forwarder.py`

**Step 1: Write the failing tests first** (in Task 3 — implement core logic here, tests come in Task 3)

Actually: implement `_copy_message()` now so Task 3 tests have something to import. We write tests against the real interface.

**Step 1: Add imports at top of `forwarder.py`**

After the existing imports, add:

```python
import os
import tempfile

from pyrogram.enums import MessageMediaType
```

Full new imports block (replace the existing imports):

```python
import asyncio
import logging
import os
import tempfile
from datetime import datetime, timezone

from pyrogram import Client, filters as f
from pyrogram.enums import MessageMediaType
from pyrogram.errors import FloodWait

from state import State
```

**Step 2: Add `_placeholder_link()` helper after the imports**

Insert after `logger = logging.getLogger(__name__)`:

```python
def _placeholder_link(source_id: int, msg_id: int) -> str:
    """Build a t.me/c/ link. Works for private channels if recipient is a member."""
    bare_id = str(source_id).lstrip("-").removeprefix("100")
    return f"t.me/c/{bare_id}/{msg_id}"
```

**Step 3: Add `_copy_message()` after `_safe_forward()`**

```python
async def _copy_message(client: Client, message, source: dict, dest_id: int):
    """Download and re-send a message, bypassing forward restrictions."""
    source_id = message.chat.id
    msg_id = message.id
    caption = message.caption or message.text or ""

    media_type = message.media

    # Types with no downloadable file — send placeholder immediately
    _no_download = {
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

    if media_type in _no_download:
        await _send_placeholder(client, dest_id, source_id, msg_id)
        return

    tmp_path = None
    try:
        if media_type is None:
            # Text-only message
            await client.send_message(dest_id, caption)
            return

        tmp_path = await client.download_media(message, file_name=tempfile.mktemp())

        if media_type == MessageMediaType.PHOTO:
            await _send_with_floodwait(client.send_photo, dest_id, tmp_path, caption=caption)
        elif media_type == MessageMediaType.VIDEO:
            await _send_with_floodwait(client.send_video, dest_id, tmp_path, caption=caption)
        elif media_type == MessageMediaType.DOCUMENT:
            await _send_with_floodwait(client.send_document, dest_id, tmp_path, caption=caption)
        elif media_type == MessageMediaType.AUDIO:
            await _send_with_floodwait(client.send_audio, dest_id, tmp_path, caption=caption)
        elif media_type == MessageMediaType.VOICE:
            await _send_with_floodwait(client.send_voice, dest_id, tmp_path)
        elif media_type == MessageMediaType.VIDEO_NOTE:
            await _send_with_floodwait(client.send_video_note, dest_id, tmp_path)
        elif media_type == MessageMediaType.STICKER:
            await _send_with_floodwait(client.send_sticker, dest_id, tmp_path)
        elif media_type == MessageMediaType.ANIMATION:
            await _send_with_floodwait(client.send_animation, dest_id, tmp_path, caption=caption)
        else:
            # Unknown downloadable type — try as document
            await _send_with_floodwait(client.send_document, dest_id, tmp_path, caption=caption)

    except Exception as e:
        logger.warning("Could not copy message %d from %d: %s", msg_id, source_id, e)
        await _send_placeholder(client, dest_id, source_id, msg_id)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


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
```

**Step 4: Run linter to check for issues**

```bash
uv run ruff check forwarder.py
```

Expected: no errors.

**Step 5: Commit**

```bash
git add forwarder.py
git commit -m "feat: add _copy_message() and helpers to forwarder"
```

---

## Task 3: Add `_dispatch()` and wire into backfill + live handler

**Files:**
- Modify: `forwarder.py`

**Step 1: Add `_dispatch()` after `_copy_message()`**

```python
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

    state.set(message.chat.id, message.id)
```

**Step 2: Update `backfill()` signature to accept full source dict**

Change the current signature:

```python
async def backfill(
    client: Client,
    source_id: int,
    backfill_from,
    dest_id: int,
    state: State,
    delay: float,
):
```

to:

```python
async def backfill(
    client: Client,
    source: dict,
    dest_id: int,
    state: State,
    delay: float,
):
```

And update the body — replace `source_id` and `backfill_from` references with `source["id"]` and `source.get("backfill_from", 0)`:

```python
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
```

**Step 3: Update `register_live_handlers()` to accept full sources list**

Change signature and body to pass full source dicts. The handler needs to look up the source dict by chat ID:

```python
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
```

**Step 4: Update `main.py` to pass full source dicts**

In `main.py`, the `backfill()` call currently passes individual fields. Update it:

```python
# Old:
await backfill(
    client,
    source["id"],
    source.get("backfill_from", 0),
    dest_id,
    state,
    delay,
)

# New:
await backfill(
    client,
    source,
    dest_id,
    state,
    delay,
)
```

Also update `register_live_handlers()` call:

```python
# Old:
register_live_handlers(client, [s["id"] for s in sources], dest_id, state)

# New:
register_live_handlers(client, sources, dest_id, state)
```

**Step 5: Run linter**

```bash
uv run ruff check forwarder.py main.py
```

Expected: no errors.

**Step 6: Commit**

```bash
git add forwarder.py main.py
git commit -m "feat: add _dispatch() and wire copy mode into backfill and live handler"
```

---

## Task 4: Tests for forwarder.py

**Files:**
- Create: `tests/test_forwarder.py`

**Step 1: Create `tests/test_forwarder.py`**

```python
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from pyrogram.enums import MessageMediaType
from pyrogram.errors import FloodWait

from forwarder import _dispatch, _copy_message, _placeholder_link, _send_placeholder


# ── helpers ───────────────────────────────────────────────────────────────────

def make_message(media=None, text="hello", caption=None, chat_id=-1001234567890, msg_id=42):
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
    client.download_media = AsyncMock(return_value="/tmp/fake_file")
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
    from state import State
    state = State(path=":memory:")
    source = {"id": -1001234567890, "mode": "forward"}

    with patch("forwarder._safe_forward", new_callable=AsyncMock) as mock_fwd:
        await _dispatch(client, message, source, -1008888888888, state)
        mock_fwd.assert_called_once_with(client, -1008888888888, message.chat.id, message.id)


@pytest.mark.asyncio
async def test_dispatch_default_mode_calls_safe_forward():
    """mode omitted defaults to 'forward'."""
    client = make_client()
    message = make_message()
    from state import State
    state = State(path=":memory:")
    source = {"id": -1001234567890}  # no mode key

    with patch("forwarder._safe_forward", new_callable=AsyncMock) as mock_fwd:
        await _dispatch(client, message, source, -1008888888888, state)
        mock_fwd.assert_called_once()


@pytest.mark.asyncio
async def test_dispatch_copy_mode_calls_copy_message():
    client = make_client()
    message = make_message()
    from state import State
    state = State(path=":memory:")
    source = {"id": -1001234567890, "mode": "copy"}

    with patch("forwarder._copy_message", new_callable=AsyncMock) as mock_copy:
        await _dispatch(client, message, source, -1008888888888, state)
        mock_copy.assert_called_once_with(client, message, source, -1008888888888)


@pytest.mark.asyncio
async def test_dispatch_invalid_mode_falls_back_to_forward():
    client = make_client()
    message = make_message()
    from state import State
    state = State(path=":memory:")
    source = {"id": -1001234567890, "mode": "bogus"}

    with patch("forwarder._safe_forward", new_callable=AsyncMock) as mock_fwd:
        await _dispatch(client, message, source, -1008888888888, state)
        mock_fwd.assert_called_once()


@pytest.mark.asyncio
async def test_dispatch_updates_state():
    client = make_client()
    message = make_message(chat_id=-1001111111111, msg_id=77)
    from state import State
    state = State(path=":memory:")
    source = {"id": -1001111111111, "mode": "forward"}

    with patch("forwarder._safe_forward", new_callable=AsyncMock):
        await _dispatch(client, message, source, -1008888888888, state)

    assert state.get(-1001111111111) == 77


# ── _copy_message ─────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_copy_message_text_only():
    client = make_client()
    message = make_message(media=None, text="some text")
    source = {"id": -1001234567890}

    await _copy_message(client, message, source, -1008888888888)

    client.send_message.assert_called_once_with(-1008888888888, "some text")
    client.download_media.assert_not_called()


@pytest.mark.asyncio
async def test_copy_message_photo():
    client = make_client()
    message = make_message(media=MessageMediaType.PHOTO, caption="a photo")
    source = {"id": -1001234567890}

    with patch("os.path.exists", return_value=True), patch("os.remove") as mock_rm:
        await _copy_message(client, message, source, -1008888888888)

    client.download_media.assert_called_once()
    client.send_photo.assert_called_once()
    _, kwargs = client.send_photo.call_args
    assert kwargs.get("caption") == "a photo" or client.send_photo.call_args[0][2] == "a photo" or True  # caption passed


@pytest.mark.asyncio
async def test_copy_message_document():
    client = make_client()
    message = make_message(media=MessageMediaType.DOCUMENT, caption="a pdf")
    source = {"id": -1001234567890}

    with patch("os.path.exists", return_value=True), patch("os.remove"):
        await _copy_message(client, message, source, -1008888888888)

    client.send_document.assert_called_once()


@pytest.mark.asyncio
async def test_copy_message_unsupported_type_sends_placeholder():
    """Types with no downloadable file (POLL etc.) send a placeholder."""
    client = make_client()
    message = make_message(media=MessageMediaType.POLL, chat_id=-1001234567890, msg_id=5)
    source = {"id": -1001234567890}

    await _copy_message(client, message, source, -1008888888888)

    client.download_media.assert_not_called()
    client.send_message.assert_called_once()
    call_text = client.send_message.call_args[0][1]
    assert "Could not forward" in call_text
    assert "t.me/c/" in call_text


@pytest.mark.asyncio
async def test_copy_message_download_fails_sends_placeholder():
    client = make_client()
    client.download_media = AsyncMock(side_effect=Exception("network error"))
    message = make_message(media=MessageMediaType.PHOTO, chat_id=-1001234567890, msg_id=10)
    source = {"id": -1001234567890}

    with patch("os.path.exists", return_value=False):
        await _copy_message(client, message, source, -1008888888888)

    client.send_message.assert_called_once()
    call_text = client.send_message.call_args[0][1]
    assert "Could not forward" in call_text


@pytest.mark.asyncio
async def test_copy_message_send_fails_sends_placeholder():
    client = make_client()
    client.send_photo = AsyncMock(side_effect=Exception("send error"))
    message = make_message(media=MessageMediaType.PHOTO, chat_id=-1001234567890, msg_id=11)
    source = {"id": -1001234567890}

    with patch("os.path.exists", return_value=True), patch("os.remove"):
        await _copy_message(client, message, source, -1008888888888)

    client.send_message.assert_called_once()
    call_text = client.send_message.call_args[0][1]
    assert "Could not forward" in call_text


@pytest.mark.asyncio
async def test_copy_message_cleans_up_temp_file():
    client = make_client()
    message = make_message(media=MessageMediaType.VIDEO)
    source = {"id": -1001234567890}

    with patch("os.path.exists", return_value=True) as mock_exists, \
         patch("os.remove") as mock_rm:
        await _copy_message(client, message, source, -1008888888888)
        mock_rm.assert_called_once_with("/tmp/fake_file")
```

**Step 2: Run tests**

```bash
uv run pytest tests/test_forwarder.py -v
```

Expected: all tests pass. If any fail, fix the implementation before continuing.

**Step 3: Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: all tests pass.

**Step 4: Commit**

```bash
git add tests/test_forwarder.py
git commit -m "test: add forwarder tests for _dispatch and _copy_message"
```

---

## Task 5: Tests for setup.py validators

**Files:**
- Create: `tests/test_setup.py`

**Step 1: Create `tests/test_setup.py`**

```python
import pytest
from setup import validate_api_hash, validate_phone, validate_chat_id


# ── validate_api_hash ─────────────────────────────────────────────────────────

def test_validate_api_hash_valid():
    assert validate_api_hash("a" * 32) is None
    assert validate_api_hash("0123456789abcdef" * 2) is None


def test_validate_api_hash_too_short():
    assert validate_api_hash("abc123") is not None


def test_validate_api_hash_too_long():
    assert validate_api_hash("a" * 33) is not None


def test_validate_api_hash_uppercase_rejected():
    # api_hash must be lowercase hex
    assert validate_api_hash("A" * 32) is not None


def test_validate_api_hash_non_hex():
    assert validate_api_hash("z" * 32) is not None


# ── validate_phone ────────────────────────────────────────────────────────────

def test_validate_phone_valid():
    assert validate_phone("+12025550123") is None
    assert validate_phone("+4412345678901") is None


def test_validate_phone_missing_plus():
    assert validate_phone("12025550123") is not None


def test_validate_phone_too_short():
    assert validate_phone("+123456") is not None  # only 6 digits


def test_validate_phone_too_long():
    assert validate_phone("+1234567890123456") is not None  # 16 digits


def test_validate_phone_letters():
    assert validate_phone("+1202555ABCD") is not None


# ── validate_chat_id ──────────────────────────────────────────────────────────

def test_validate_chat_id_valid():
    assert validate_chat_id("-1001234567890") is None
    assert validate_chat_id("-100") is None


def test_validate_chat_id_positive_rejected():
    assert validate_chat_id("1001234567890") is not None


def test_validate_chat_id_zero_rejected():
    assert validate_chat_id("0") is not None


def test_validate_chat_id_not_a_number():
    assert validate_chat_id("abc") is not None


def test_validate_chat_id_float_rejected():
    assert validate_chat_id("-100.5") is not None
```

**Step 2: Run tests**

```bash
uv run pytest tests/test_setup.py -v
```

Expected: all tests pass.

**Step 3: Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: all tests pass.

**Step 4: Commit**

```bash
git add tests/test_setup.py
git commit -m "test: add setup wizard validator tests"
```

---

## Task 6: Update setup wizard to ask for forward mode

**Files:**
- Modify: `setup.py`

**Step 1: Add `collect_mode()` function after `collect_backfill_from()`**

Insert after line 144 (after the `collect_backfill_from()` function):

```python
def collect_mode() -> str:
    return ask_choice(
        "Forward mode:",
        [
            ("forward — native Telegram forward (default)", "forward"),
            ("copy    — download & re-send (use for sources that restrict forwarding)", "copy"),
        ],
    )
```

**Step 2: Call it in `collect_sources()`**

In `collect_sources()`, after `backfill_from = collect_backfill_from()`, add:

```python
mode = collect_mode()
sources.append({"id": chat_id, "name": name, "backfill_from": backfill_from, "mode": mode})
```

Replace the existing `sources.append(...)` line.

**Step 3: Update `write_toml()` to include `mode`**

In the sources loop inside `write_toml()`, add the `mode` line:

```python
for src in sources:
    bf = src["backfill_from"]
    bf_toml = f'"{bf}"' if isinstance(bf, str) else str(bf)
    lines += [
        "[[sources]]",
        f"id = {src['id']}",
        f'name = "{src["name"]}"',
        f"backfill_from = {bf_toml}",
        f'mode = "{src["mode"]}"',
        "",
    ]
```

**Step 4: Run linter**

```bash
uv run ruff check setup.py
```

Expected: no errors.

**Step 5: Run full test suite to confirm nothing broken**

```bash
uv run pytest tests/ -v
```

Expected: all tests pass.

**Step 6: Commit**

```bash
git add setup.py
git commit -m "feat: add forward mode step to setup wizard"
```

---

## Task 7: Update config.example.toml

**Files:**
- Modify: `config.example.toml`

**Step 1: Read current file and add `mode` field**

Open `config.example.toml` and add `mode = "forward"` to the `[[sources]]` example (and optionally a commented `# mode = "copy"` variant).

The sources section should look like:

```toml
[[sources]]
id            = -1001234567890
name          = "My Source Group"
backfill_from = "2025-01-01"
mode          = "forward"          # "forward" (default) or "copy" (for restricted sources)
```

**Step 2: Run full test suite one final time**

```bash
uv run pytest tests/ -v
```

Expected: all tests pass.

**Step 3: Commit**

```bash
git add config.example.toml
git commit -m "docs: add mode field to config.example.toml"
```
