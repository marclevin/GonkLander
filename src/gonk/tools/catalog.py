"""Load the tool catalog."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from gonk.core.errors import GonkError

USER_CATALOG = "tools.yaml"
KNOWN_KEYS = {"description", "check", "requires", "install", "note"}
KNOWN_CHECK_KEYS = {"commands", "version_args", "min_version"}


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str = ""
    commands: tuple[str, ...] = ()
    version_args: tuple[str, ...] = ("--version",)
    min_version: str = ""
    requires: tuple[str, ...] = ()
    install: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    note: str = ""


def _words(value: Any, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise GonkError(f"{where} should be a list of words.")
    return tuple(value)


def parse_tool(name: str, data: Any, source: str) -> ToolSpec:
    where = f"{source}: tool '{name}'"
    if not isinstance(data, dict):
        raise GonkError(f"{where} should be a set of settings.")
    if unknown := set(data) - KNOWN_KEYS:
        raise GonkError(f"{where} has unknown setting(s): {', '.join(sorted(unknown))}.")

    check = data.get("check", {})
    if not isinstance(check, dict):
        raise GonkError(f"{where}: check should be a set of settings.")
    if unknown := set(check) - KNOWN_CHECK_KEYS:
        raise GonkError(f"{where}: check has unknown setting(s): {', '.join(sorted(unknown))}.")

    install = data.get("install", {})
    if not isinstance(install, dict) or not all(
        isinstance(options, dict) for options in install.values()
    ):
        raise GonkError(f"{where}: install should map provider names to their options.")

    return ToolSpec(
        name=name,
        description=str(data.get("description", "")),
        commands=_words(check.get("commands", [name]), f"{where}: check.commands"),
        version_args=_words(check.get("version_args", ["--version"]), f"{where}: version_args"),
        min_version=str(check.get("min_version", "")),
        requires=_words(data.get("requires", []), f"{where}: requires"),
        install={str(provider): dict(options) for provider, options in install.items()},
        note=str(data.get("note", "")),
    )


def parse_catalog(text: str, source: str) -> dict[str, ToolSpec]:
    try:
        data = yaml.safe_load(text) or {}
    except yaml.YAMLError as error:
        raise GonkError(f"{source} is not valid YAML: {error}") from error
    if not isinstance(data, dict):
        raise GonkError(f"{source} should map tool names to their settings.")
    return {str(name): parse_tool(str(name), spec, source) for name, spec in data.items()}


def load_catalog(user_directory: Path | None = None) -> dict[str, ToolSpec]:
    """The built-in catalog, with the user's own tools layered on top."""
    builtin = resources.files("gonk.data").joinpath("tools.yaml").read_text(encoding="utf-8")
    catalog = parse_catalog(builtin, "built-in tools.yaml")
    if user_directory is not None:
        path = user_directory / USER_CATALOG
        if path.is_file():
            catalog.update(parse_catalog(path.read_text(encoding="utf-8"), str(path)))
    return catalog
