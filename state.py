import json
import logging
import os

logger = logging.getLogger(__name__)


class State:
    def __init__(self, path: str):
        self._path = path
        self._data: dict[str, int] = {}
        if path != ":memory:" and os.path.exists(path):
            with open(path) as f:
                try:
                    self._data = json.load(f)
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "state file %r is corrupted (%s); starting with empty state",
                        path,
                        exc,
                    )

    def get(self, source_id: int) -> int:
        return self._data.get(str(source_id), 0)

    def set(self, source_id: int, message_id: int) -> None:
        self._data[str(source_id)] = message_id
        if self._path != ":memory:":
            tmp = self._path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp, self._path)
