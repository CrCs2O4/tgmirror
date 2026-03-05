# Telegram Group Forwarder — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build a persistent Python daemon that backfills and live-forwards all messages from invite-only Telegram groups to a destination channel/group.

**Architecture:** Pyrogram user-client, backfill-then-live two-phase startup, native `forward_messages()`, state persisted to JSON file.

**Tech Stack:** Python 3.11+, Pyrogram 2.x, tgcrypto, tomllib (stdlib in 3.11+)

---

### Task 1: Project scaffold

**Files:**
- Create: `telegram-forwarder/.gitignore`
- Create: `telegram-forwarder/requirements.txt`
- Create: `telegram-forwarder/config.example.toml`

**Step 1: Create the project directory**

```bash
mkdir telegram-forwarder
```

**Step 2: Create `telegram-forwarder/requirements.txt`**

```
pyrogram==2.0.106
tgcrypto
tomli; python_version < "3.11"
```

**Step 3: Create `telegram-forwarder/.gitignore`**

```
*.session
*.session-journal
config.toml
state.json
__pycache__/
.venv/
*.pyc
```

**Step 4: Create `telegram-forwarder/config.example.toml`**

```toml
[telegram]
api_id = 12345
api_hash = "your_api_hash_here"
phone = "+1234567890"

[settings]
backfill_delay_seconds = 0.5

[[sources]]
id = -1001234567890
name = "My Source Group"
backfill_from = "2025-01-01"

[[sources]]
id = -1009999999999
name = "Another Group"
backfill_from = 0

[destination]
id = -1008888888888
name = "My Channel"
```

**Step 5: Commit**

```bash
git add telegram-forwarder/
git commit -m "feat: scaffold telegram-forwarder project"
```

---

### Task 2: `state.py` — persist last-forwarded message ID

**Files:**
- Create: `telegram-forwarder/state.py`
- Create: `telegram-forwarder/tests/__init__.py`
- Create: `telegram-forwarder/tests/test_state.py`

**Step 1: Write the failing tests**

Create `telegram-forwarder/tests/test_state.py`:

```python
import json
import pytest
from state import State


def test_get_returns_zero_for_unknown_source():
    s = State(path=":memory:")
    assert s.get(-1001234) == 0


def test_set_and_get():
    s = State(path=":memory:")
    s.set(-1001234, 9999)
    assert s.get(-1001234) == 9999


def test_set_overwrites_previous():
    s = State(path=":memory:")
    s.set(-1001234, 100)
    s.set(-1001234, 200)
    assert s.get(-1001234) == 200


def test_multiple_sources_independent():
    s = State(path=":memory:")
    s.set(-1001111, 10)
    s.set(-1002222, 20)
    assert s.get(-1001111) == 10
    assert s.get(-1002222) == 20


def test_persists_to_file(tmp_path):
    path = str(tmp_path / "state.json")
    s = State(path=path)
    s.set(-1001234, 42)
    s2 = State(path=path)
    assert s2.get(-1001234) == 42


def test_loads_existing_file(tmp_path):
    path = str(tmp_path / "state.json")
    with open(path, "w") as f:
        json.dump({"-1001234": 99}, f)
    s = State(path=path)
    assert s.get(-1001234) == 99
```

**Step 2: Run tests to verify they fail**

```bash
cd telegram-forwarder && python -m pytest tests/test_state.py -v
```

Expected: `ModuleNotFoundError: No module named 'state'`

**Step 3: Implement `telegram-forwarder/state.py`**

```python
import json
import os


class State:
    def __init__(self, path: str):
        self._path = path
        self._data: dict[str, int] = {}
        if path != ":memory:" and os.path.exists(path):
            with open(path) as f:
                self._data = json.load(f)

    def get(self, source_id: int) -> int:
        return self._data.get(str(source_id), 0)

    def set(self, source_id: int, message_id: int) -> None:
        self._data[str(source_id)] = message_id
        if self._path != ":memory:":
            with open(self._path, "w") as f:
                json.dump(self._data, f, indent=2)
```

**Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_state.py -v
```

Expected: all 6 tests PASS

**Step 5: Commit**

```bash
git add telegram-forwarder/
git commit -m "feat: add state persistence module"
```

---

### Task 3: `client.py` — Pyrogram session setup

**Files:**
- Create: `telegram-forwarder/client.py`

**Step 1: Install dependencies**

```bash
cd telegram-forwarder && python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

**Step 2: Implement `telegram-forwarder/client.py`**

```python
import pyrogram


def build_client(config: dict) -> pyrogram.Client:
    """Build a Pyrogram user client from config dict."""
    tg = config["telegram"]
    return pyrogram.Client(
        name="forwarder",
        api_id=tg["api_id"],
        api_hash=tg["api_hash"],
        phone_number=tg["phone"],
    )
```

No unit test — requires live Telegram credentials. Tested implicitly in Task 7 smoke test.

**Step 3: Commit**

```bash
git add telegram-forwarder/client.py
git commit -m "feat: add pyrogram client factory"
```

---

### Task 4: `forwarder.py` — backfill logic

**Files:**
- Create: `telegram-forwarder/forwarder.py`

**Step 1: Implement backfill in `telegram-forwarder/forwarder.py`**

```python
import asyncio
import logging
import time
from datetime import datetime, timezone

from pyrogram import Client
from pyrogram.errors import FloodWait

from state import State

logger = logging.getLogger(__name__)


def _resolve_offset_date(backfill_from) -> datetime | None:
    """Convert backfill_from config value to a datetime or None."""
    if backfill_from == 0:
        return None
    if isinstance(backfill_from, str):
        return datetime.fromisoformat(backfill_from).replace(tzinfo=timezone.utc)
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

    logger.info("Backfilling source %d from %s (last_id=%d)", source_id, backfill_from, last_id)

    async for message in client.get_chat_history(source_id, offset_date=offset_date):
        if message.id <= min_msg_id:
            break
        await _safe_forward(client, dest_id, source_id, message.id)
        state.set(source_id, message.id)
        logger.debug("Forwarded message %d from %d", message.id, source_id)
        await asyncio.sleep(delay)

    logger.info("Backfill complete for source %d", source_id)
```

**Step 2: Commit**

```bash
git add telegram-forwarder/forwarder.py
git commit -m "feat: add backfill logic"
```

---

### Task 5: `forwarder.py` — live handler

**Step 1: Add `register_live_handlers` to `telegram-forwarder/forwarder.py`**

Append to the existing file:

```python
from pyrogram import filters as f


def register_live_handlers(client: Client, source_ids: list[int], dest_id: int, state: State):
    """Register a message handler that forwards new messages in real time."""

    @client.on_message(f.chat(source_ids))
    async def handler(c: Client, message):
        try:
            await _safe_forward(c, dest_id, message.chat.id, message.id)
            state.set(message.chat.id, message.id)
            logger.debug("Live-forwarded message %d from %d", message.id, message.chat.id)
        except Exception as e:
            logger.error("Failed to forward message %d: %s", message.id, e)
```

**Step 2: Commit**

```bash
git add telegram-forwarder/forwarder.py
git commit -m "feat: add live message forwarding handler"
```

---

### Task 6: `main.py` — entry point

**Files:**
- Create: `telegram-forwarder/main.py`

**Step 1: Implement `telegram-forwarder/main.py`**

```python
import asyncio
import logging
import sys

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

from client import build_client
from forwarder import backfill, register_live_handlers
from state import State

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


async def run():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.toml"
    with open(config_path, "rb") as f:
        config = tomllib.load(f)

    dest_id = config["destination"]["id"]
    delay = config.get("settings", {}).get("backfill_delay_seconds", 0.5)
    sources = config["sources"]
    state = State("state.json")
    client = build_client(config)

    async with client:
        for source in sources:
            try:
                await backfill(
                    client,
                    source["id"],
                    source.get("backfill_from", 0),
                    dest_id,
                    state,
                    delay,
                )
            except Exception as e:
                logger.error("Backfill failed for source %s: %s", source.get("name", source["id"]), e)

        register_live_handlers(client, [s["id"] for s in sources], dest_id, state)
        logger.info("Live monitoring started. Press Ctrl+C to stop.")
        await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(run())
```

**Step 2: Commit**

```bash
git add telegram-forwarder/main.py
git commit -m "feat: add main entry point"
```

---

### Task 7: Manual smoke test

**Step 1: Copy and fill config**

```bash
cd telegram-forwarder
cp config.example.toml config.toml
# edit config.toml with real api_id, api_hash, phone, source IDs, dest ID
```

Get `api_id` and `api_hash` from https://my.telegram.org/apps

**Step 2: Run and auth**

```bash
source .venv/bin/activate
python main.py
# Enter phone number + OTP when prompted
# Enter 2FA password if applicable
```

**Step 3: Verify backfill**

Check destination channel — messages from source groups should appear.

**Step 4: Verify live forwarding**

Send a test message in a source group. It should appear in the destination within seconds.

**Step 5: Verify restart safety**

Kill with `Ctrl+C`, restart `python main.py`. Confirm no duplicate messages in destination.

**Step 6: Commit README**

```bash
git add telegram-forwarder/
git commit -m "docs: add README with setup and usage"
```

---

### Task 8 (Optional): systemd service for server deployment

**Files:**
- Create: `telegram-forwarder/telegram-forwarder.service`

```ini
[Unit]
Description=Telegram Group Forwarder
After=network.target

[Service]
Type=simple
WorkingDirectory=/opt/telegram-forwarder
ExecStart=/opt/telegram-forwarder/.venv/bin/python main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Deploy steps:**
1. Copy project to server: `rsync -av telegram-forwarder/ user@server:/opt/telegram-forwarder/`
2. Copy `.session` file to server (auth locally first)
3. Install service: `sudo cp telegram-forwarder.service /etc/systemd/system/`
4. Enable: `sudo systemctl enable --now telegram-forwarder`
5. Check logs: `sudo journalctl -u telegram-forwarder -f`

**Commit:**
```bash
git add telegram-forwarder/telegram-forwarder.service
git commit -m "feat: add systemd service file for server deployment"
```
