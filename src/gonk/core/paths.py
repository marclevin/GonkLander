"""Where Gonk keeps its files, per platform."""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path


def _platform_key() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def config_dir(env: Mapping[str, str], home: Path, platform_key: str | None = None) -> Path:
    """Directory for config.yaml, credentials, plugins and user profiles."""
    if override := env.get("GONK_CONFIG_DIR"):
        return Path(override).expanduser()
    key = platform_key or _platform_key()
    if key == "windows":
        base = env.get("APPDATA")
        return (Path(base) if base else home / "AppData" / "Roaming") / "gonk"
    if key == "macos":
        return home / "Library" / "Application Support" / "gonk"
    base = env.get("XDG_CONFIG_HOME")
    return (Path(base) if base else home / ".config") / "gonk"


def state_dir(env: Mapping[str, str], home: Path, platform_key: str | None = None) -> Path:
    """Directory for state and the audit log."""
    if override := env.get("GONK_STATE_DIR"):
        return Path(override).expanduser()
    key = platform_key or _platform_key()
    if key == "windows":
        base = env.get("LOCALAPPDATA")
        return (Path(base) if base else home / "AppData" / "Local") / "gonk"
    if key == "macos":
        return home / "Library" / "Application Support" / "gonk"
    base = env.get("XDG_STATE_HOME")
    return (Path(base) if base else home / ".local" / "state") / "gonk"


def ensure_directory(path: Path) -> None:
    """Create a directory that only the owner can enter. Existing ones are left alone."""
    path.mkdir(parents=True, exist_ok=True, mode=0o700)


def write_private(path: Path, text: str) -> None:
    """Write a file that only the owner can read, replacing it atomically."""
    ensure_directory(path.parent)
    temporary = path.with_name(path.name + ".tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)
