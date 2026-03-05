# tgmirror

📡 Mirror Telegram groups and channels to a destination in real time.

Runs a backfill phase on startup to catch up on any missed messages, then switches to live forwarding as new messages arrive. Supports native forwarding or copy mode (download + re-upload) per source, useful for channels that restrict forwarding.

See [CHANGELOG.md](CHANGELOG.md) for version history.

## Requirements

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) (installed automatically by `make setup`)
- A Telegram account
- Telegram API credentials (api_id and api_hash)

## Setup

### 1. Get API credentials

Visit https://my.telegram.org/apps, log in, and create an application. Note your **api_id** (integer) and **api_hash** (string).

### 2. Configure

```bash
cp config.example.toml config.toml
```

Edit `config.toml` and fill in your credentials and group/channel IDs (see Config Reference below).

### 3. Find group and channel IDs

Telegram IDs for supergroups and channels are negative integers (e.g. `-1001234567890`).

**Option A:** Forward any message from the group/channel to [@userinfobot](https://t.me/userinfobot) — it will show the chat ID in the reply.

**Option B:** Use a Telegram client that displays IDs (e.g. Telegram Desktop with developer mode, or the `pyrogram` shell).

### 4. Install dependencies

```bash
make setup
```

This installs `uv` if it isn't already on your `PATH`, then syncs all dependencies into `.venv`.

### 5. First run — authenticate

```bash
make run
```

On first run you will be prompted for your phone number and the OTP sent by Telegram. A `.session` file is created and reused on subsequent runs.

## Makefile Reference

```
make setup         Install uv (if missing) and sync all dependencies
make install       Sync dependencies into .venv via uv (requires uv)
make run           Run the forwarder (CONFIG=path/to/config.toml)
make debug         Run with LOG_LEVEL=DEBUG (tgmirror logs only)
make test          Run tests
make lint          Run ruff linter
make fmt           Format code with ruff
make docker-build  Build Docker image
make docker-run    Run via Docker, interactive (DATA_DIR=<dir>, IMAGE=<name>)
```

Pass a custom config path with `CONFIG=`:

```bash
make run CONFIG=/path/to/my-config.toml
```

## Docker

Docker is useful for running on a server. Because Pyrogram needs to prompt for phone/OTP on first auth, the container is run interactively.

### 1. Build the image

```bash
make docker-build
```

### 2. Prepare a data directory

```bash
mkdir ~/tg-data
cp config.example.toml ~/tg-data/config.toml
# edit ~/tg-data/config.toml
```

### 3. First run — authenticate

```bash
make docker-run DATA_DIR=~/tg-data
```

Enter your phone number and OTP when prompted. The `forwarder.session` file is written to `DATA_DIR` and reused on subsequent runs.

### 4. Subsequent runs

```bash
make docker-run DATA_DIR=~/tg-data
```

## Config Reference

```toml
[telegram]
api_id = 12345          # Integer app ID from my.telegram.org/apps
api_hash = "abc123"     # Hex string from my.telegram.org/apps
phone = "+1234567890"   # Your Telegram account phone number

[settings]
backfill_delay_seconds = 0.5  # Pause between forwarded messages during backfill
                               # Increase if you hit rate limits

[[sources]]
id = -1001234567890     # Telegram chat ID of the source group/channel
name = "My Group"       # Human-readable label (used in logs only)
backfill_from = "2025-01-01"  # ISO date: backfill messages from this date onward
                               # Use 0 to skip backfill, or a message ID integer
                               # to start from that message
mode = "forward"        # "forward" (default) — native Telegram forward
                        # "copy" — download and re-upload (bypasses forward restrictions)

[destination]
id = -1008888888888     # Telegram chat ID of the destination channel
name = "My Channel"     # Human-readable label (used in logs only)
```

Multiple `[[sources]]` sections can be added to forward from several groups.

## Server Deployment

### 1. Authenticate locally first

Run `make run` on your local machine to complete the phone/OTP flow and generate the `.session` file. **Do not skip this step** — the systemd service cannot prompt interactively.

### 2. Copy files to the server

```bash
rsync -av --exclude='.venv' --exclude='__pycache__' \
  tgmirror/ user@server:/opt/tgmirror/
```

### 3. Install dependencies on the server

```bash
ssh user@server
cd /opt/tgmirror
make setup
```

### 4. Install and enable the systemd unit

```bash
sudo cp /opt/tgmirror/tgmirror.service \
        /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now tgmirror
```

### 5. Check status and logs

```bash
sudo systemctl status tgmirror
sudo journalctl -u tgmirror -f
```

## How It Works

On startup the forwarder runs a **backfill phase**: for each source it collects all messages after the configured `backfill_from` point (newest→oldest), then dispatches them oldest-first so the destination channel receives them in chronological order. Progress is saved after each message so restarts resume cleanly without re-sending anything.

Once backfill is complete it enters the **live phase**: a Pyrogram message handler fires for every new message in any source chat and forwards it to the destination immediately.

**Forward mode** (`mode = "forward"`, default) uses Telegram's native forward API. **Copy mode** (`mode = "copy"`) downloads each message to a temporary file and re-uploads it, which works around sources that have disabled forwarding. Photos and documents are supported; other media types produce a placeholder link pointing to the original message.
