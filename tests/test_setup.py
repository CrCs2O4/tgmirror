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
