"""Load profiles: named lists of tools."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from gonk.core.errors import GonkError

KNOWN_KEYS = {"name", "description", "extends", "tools"}


@dataclass(frozen=True)
class Profile:
    name: str
    description: str = ""
    extends: str = ""
    tools: tuple[str, ...] = ()


def parse_profile(text: str, source: str) -> Profile:
    try:
        data: Any = yaml.safe_load(text)
    except yaml.YAMLError as error:
        raise GonkError(f"{source} is not valid YAML: {error}") from error
    if not isinstance(data, dict):
        raise GonkError(f"{source} should contain name, description and tools.")
    if unknown := set(data) - KNOWN_KEYS:
        raise GonkError(f"{source} has unknown setting(s): {', '.join(sorted(unknown))}.")
    if not isinstance(data.get("name"), str) or not data["name"]:
        raise GonkError(f"{source} needs a name.")
    tools = data.get("tools", [])
    if not isinstance(tools, list) or not all(isinstance(tool, str) for tool in tools):
        raise GonkError(f"{source}: tools should be a list of tool names.")
    return Profile(
        name=data["name"],
        description=str(data.get("description", "")),
        extends=str(data.get("extends") or ""),
        tools=tuple(tools),
    )


def load_profiles(user_directory: Path | None = None) -> dict[str, Profile]:
    """Built-in profiles, with the user's own layered on top."""
    profiles: dict[str, Profile] = {}
    builtin = resources.files("gonk.data").joinpath("profiles")
    for entry in sorted(builtin.iterdir(), key=lambda item: item.name):
        if entry.name.endswith(".yaml"):
            profile = parse_profile(entry.read_text(encoding="utf-8"), f"built-in {entry.name}")
            profiles[profile.name] = profile
    if user_directory is not None and (user_directory / "profiles").is_dir():
        for path in sorted((user_directory / "profiles").glob("*.yaml")):
            profile = parse_profile(path.read_text(encoding="utf-8"), str(path))
            profiles[profile.name] = profile
    return profiles


def resolve_tools(name: str, profiles: dict[str, Profile]) -> list[str]:
    """Every tool in a profile, inherited ones first, each listed once."""
    chain: list[Profile] = []
    current = name
    while current:
        if current not in profiles:
            available = ", ".join(sorted(profiles))
            if current == name:
                raise GonkError(
                    f"There is no profile called '{name}'.",
                    hints=[f"Available profiles: {available}"],
                )
            raise GonkError(
                f"Profile '{chain[-1].name}' extends '{current}', which does not exist."
            )
        if any(profile.name == current for profile in chain):
            loop = " → ".join([*(profile.name for profile in chain), current])
            raise GonkError(f"Profiles extend each other in a loop: {loop}.")
        chain.append(profiles[current])
        current = profiles[current].extends

    tools: list[str] = []
    for profile in reversed(chain):
        for tool in profile.tools:
            if tool not in tools:
                tools.append(tool)
    return tools
