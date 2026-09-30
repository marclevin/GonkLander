"""An append-only record of things worth being able to look up later."""

from __future__ import annotations

import contextlib
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_VALUE_LENGTH = 200


def _clip(value: Any) -> Any:
    """Shorten long values and remove control characters.

    Some of what is recorded comes from callers. Without this, a crafted
    capability name could carry terminal escape sequences into `gonk log`.
    """
    if isinstance(value, str):
        text = "".join(char if char.isprintable() else "?" for char in value)
        return text[:MAX_VALUE_LENGTH] + "…" if len(text) > MAX_VALUE_LENGTH else text
    if isinstance(value, dict):
        return {_clip(str(key)): _clip(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clip(item) for item in value]
    return value


class AuditLog:
    def __init__(self, path: Path) -> None:
        self.path = path

    def record(self, event: str, **fields: Any) -> None:
        """Append one event. Raises OSError if the log cannot be written.

        Callers guarding a capability let that propagate: a call that cannot
        be recorded does not happen.
        """
        entry = {"time": datetime.now(UTC).isoformat(timespec="seconds"), "event": event}
        entry.update({key: _clip(value) for key, value in fields.items()})
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        descriptor = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, default=str) + "\n")

    def record_quietly(self, event: str, **fields: Any) -> None:
        """For the CLI's own bookkeeping, where a read-only disk should not stop work."""
        with contextlib.suppress(OSError):
            self.record(event, **fields)

    def tail(self, count: int = 20) -> list[dict[str, Any]]:
        try:
            lines = self.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        entries = []
        for line in lines[-count:]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries
