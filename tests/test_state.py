import json
import os
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


def test_handles_corrupted_json(tmp_path):
    path = str(tmp_path / "state.json")
    with open(path, "w") as f:
        f.write("{invalid json")
    # Should not raise — starts with empty state
    s = State(path=path)
    assert s.get(-1001234) == 0


def test_memory_sentinel_does_not_create_file():
    s = State(path=":memory:")
    s.set(-1001234, 42)
    assert not os.path.exists(":memory:")
