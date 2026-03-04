import asyncio
import logging
import os
import sys

from pyrogram import idle

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
            except Exception:
                logger.exception(
                    "Backfill failed for source %s",
                    source.get("name", source["id"]),
                )

        register_live_handlers(client, [s["id"] for s in sources], dest_id, state)
        logger.info("Live monitoring started. Press Ctrl+C to stop.")
        await idle()


if __name__ == "__main__":
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.toml"
    if not os.path.exists(config_path):
        import setup  # noqa: PLC0415

        setup.main()
    else:
        asyncio.run(run())
