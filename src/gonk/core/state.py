"""Small facts Gonk remembers between runs, such as which profile was landed."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

STATE_FILE = "state.json"


def load(directory: Path) -> dict[str, Any]:
    try:
        data = json.loads((directory / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def update(directory: Path, **values: Any) -> None:
    """Best effort: losing this file costs nothing but a line in `gonk status`."""
    data = {**load(directory), **values}
    try:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / STATE_FILE).write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except OSError:
        pass
