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
