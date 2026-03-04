#!/usr/bin/env python3
"""Interactive setup wizard — creates config.toml and optionally launches the forwarder."""

import os
import re
import subprocess
import sys

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

# ── helpers ──────────────────────────────────────────────────────────────────


def ask(prompt: str, default: str = "") -> str:
    """Prompt for a string value, showing a default if provided."""
    if default:
        display = f"{prompt} [{default}]: "
    else:
        display = f"{prompt}: "
    while True:
        value = input(display).strip()
        if value:
            return value
        if default:
            return default
        print("  This field is required.")


def ask_int(prompt: str, default: int | None = None) -> int:
    """Prompt for an integer value."""
    default_str = str(default) if default is not None else ""
    while True:
        raw = ask(prompt, default_str)
        try:
            return int(raw)
        except ValueError:
            print("  Please enter a whole number.")


def ask_choice(prompt: str, choices: list[tuple[str, str]]) -> str:
    """Present a lettered menu and return the chosen value."""
    print(prompt)
    letters = []
    for i, (label, _value) in enumerate(choices):
        letter = chr(ord("a") + i)
        letters.append(letter)
        print(f"  {letter}) {label}")
    while True:
        raw = input("Choice: ").strip().lower()
        if raw in letters:
            return choices[ord(raw) - ord("a")][1]
        print(f"  Enter one of: {', '.join(letters)}")


def ask_yes_no(prompt: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{prompt} [{hint}]: ").strip().lower()
        if not raw:
            return default
        if raw in ("y", "yes"):
            return True
        if raw in ("n", "no"):
            return False
        print("  Enter y or n.")


def section(title: str) -> None:
    print(f"\n── {title} {'─' * max(0, 50 - len(title))}")


def validate_api_hash(value: str) -> str | None:
    """Return error message or None if valid."""
    if not re.fullmatch(r"[0-9a-f]{32}", value):
        return "api_hash must be a 32-character hex string (from my.telegram.org/apps)"
    return None


def validate_phone(value: str) -> str | None:
    if not re.fullmatch(r"\+\d{7,15}", value):
        return "Phone must start with + followed by 7–15 digits (e.g. +12025550123)"
    return None


def validate_chat_id(value: str) -> str | None:
    try:
        n = int(value)
    except ValueError:
        return "Must be an integer (e.g. -1001234567890)"
    if n >= 0:
        return "Chat IDs for groups/channels are negative integers"
    return None


def ask_validated(prompt: str, validator, default: str = "") -> str:
    while True:
        value = ask(prompt, default)
        error = validator(value)
        if error is None:
            return value
        print(f"  {error}")


# ── section collectors ────────────────────────────────────────────────────────


def collect_telegram() -> dict:
    section("Telegram credentials  (https://my.telegram.org/apps)")
    api_id = ask_int("api_id (integer)")
    api_hash = ask_validated("api_hash (32-char hex)", validate_api_hash)
    phone = ask_validated("phone number (e.g. +12025550123)", validate_phone)
    return {"api_id": api_id, "api_hash": api_hash, "phone": phone}


def collect_settings() -> dict:
    section("Settings")
    delay = ask("Backfill delay between messages in seconds", default="0.5")
    try:
        delay = float(delay)
    except ValueError:
        delay = 0.5
    return {"backfill_delay_seconds": delay}


def collect_backfill_from() -> str | int:
    choice = ask_choice(
        "Backfill — where to start forwarding from:",
        [
            ("A specific date  (e.g. 2025-01-01)", "date"),
            ("A specific message ID", "msgid"),
            ("Skip backfill (live messages only)", "skip"),
        ],
    )
    if choice == "date":
        while True:
            raw = ask("Start date (YYYY-MM-DD)")
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
                return raw
            print("  Use YYYY-MM-DD format, e.g. 2025-01-01")
    elif choice == "msgid":
        while True:
            raw = ask("Message ID (positive integer)")
            try:
                n = int(raw)
                if n > 0:
                    return n
            except ValueError:
                pass
            print("  Must be a positive integer.")
    else:
        return 0


def collect_sources() -> list[dict]:
    section("Source groups / channels")
    print("Add the groups or channels you want to forward FROM.")
    sources = []
    while True:
        print(f"\n  Source #{len(sources) + 1}")
        chat_id = int(ask_validated("  Chat ID (negative integer)", validate_chat_id))
        name = ask("  Name (label for logs)")
        backfill_from = collect_backfill_from()
        sources.append({"id": chat_id, "name": name, "backfill_from": backfill_from})
        if not ask_yes_no("\nAdd another source?", default=False):
            break
    return sources


def collect_destination() -> dict:
    section("Destination channel / group")
    print("The channel or group you want to forward TO.")
    chat_id = int(ask_validated("Chat ID (negative integer)", validate_chat_id))
    name = ask("Name (label for logs)")
    return {"id": chat_id, "name": name}


# ── TOML writer ───────────────────────────────────────────────────────────────


def write_toml(
    path: str, telegram: dict, settings: dict, sources: list[dict], destination: dict
) -> None:
    lines = [
        "[telegram]",
        f"api_id = {telegram['api_id']}",
        f'api_hash = "{telegram["api_hash"]}"',
        f'phone = "{telegram["phone"]}"',
        "",
        "[settings]",
        f"backfill_delay_seconds = {settings['backfill_delay_seconds']}",
        "",
    ]
    for src in sources:
        bf = src["backfill_from"]
        bf_toml = f'"{bf}"' if isinstance(bf, str) else str(bf)
        lines += [
            "[[sources]]",
            f"id = {src['id']}",
            f'name = "{src["name"]}"',
            f"backfill_from = {bf_toml}",
            "",
        ]
    lines += [
        "[destination]",
        f"id = {destination['id']}",
        f'name = "{destination["name"]}"',
        "",
    ]
    with open(path, "w") as f:
        f.write("\n".join(lines))


# ── main ──────────────────────────────────────────────────────────────────────


def main() -> None:
    config_path = "config.toml"

    print("━" * 54)
    print("  tgmirror — setup wizard")
    print("━" * 54)

    if os.path.exists(config_path):
        print(f"\n{config_path} already exists.")
        if not ask_yes_no("Overwrite it?", default=False):
            print("Keeping existing config.")
            if ask_yes_no("\nRun the forwarder now?", default=True):
                os.execvp("make", ["make", "run"])
            sys.exit(0)

    telegram = collect_telegram()
    settings = collect_settings()
    sources = collect_sources()
    destination = collect_destination()

    write_toml(config_path, telegram, settings, sources, destination)
    print(f"\n✓ Written to {config_path}")

    # Sanity-check: re-parse what we just wrote
    try:
        with open(config_path, "rb") as f:
            tomllib.load(f)
    except Exception as e:
        print(f"\nWarning: the generated config may have an issue: {e}")
        print("Please review config.toml before running.")
        sys.exit(1)

    if ask_yes_no("\nRun the forwarder now?", default=True):
        os.execvp("make", ["make", "run"])


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nAborted.")
        sys.exit(1)
