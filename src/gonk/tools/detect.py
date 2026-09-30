"""Is a tool installed, and is it new enough?"""

from __future__ import annotations

import re
from dataclasses import dataclass

from gonk.core.system import System
from gonk.tools.catalog import ToolSpec

VERSION_PATTERN = re.compile(r"(\d+(?:\.\d+)+|\d+)")


@dataclass(frozen=True)
class Detection:
    tool: str
    present: bool
    path: str = ""
    version: str = ""
    acceptable: bool = False


def parse_version(text: str) -> tuple[int, ...] | None:
    """The first thing in `text` that looks like a version number."""
    # Prefer dotted versions: "OpenSSH_9.6p1" and "jq-1.7" before a stray digit.
    dotted = re.search(r"\d+(?:\.\d+)+", text)
    match = dotted or VERSION_PATTERN.search(text)
    if match is None:
        return None
    return tuple(int(part) for part in match.group(0).split("."))


def _pad(parts: tuple[int, ...], width: int) -> tuple[int, ...]:
    """1.6 and 1.6.0 are the same version."""
    return parts + (0,) * (width - len(parts))


def version_at_least(found: str, wanted: str) -> bool:
    """True if `found` is `wanted` or newer. Unknown versions are given the benefit
    of the doubt: Gonk does not reinstall something it cannot measure."""
    if not wanted:
        return True
    found_parts, wanted_parts = parse_version(found), parse_version(wanted)
    if found_parts is None or wanted_parts is None:
        return True
    width = max(len(found_parts), len(wanted_parts))
    return _pad(found_parts, width) >= _pad(wanted_parts, width)


def detect(spec: ToolSpec, system: System) -> Detection:
    for command in spec.commands:
        path = system.which(command)
        if path is None:
            continue
        result = system.run([command, *spec.version_args], timeout=10)
        parsed = parse_version(result.text.splitlines()[0]) if result.text else None
        version = ".".join(str(part) for part in parsed) if parsed else ""
        return Detection(
            tool=spec.name,
            present=True,
            path=path,
            version=version,
            acceptable=version_at_least(version, spec.min_version),
        )
    return Detection(tool=spec.name, present=False)
