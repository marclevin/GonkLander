"""Decide what to do about each requested tool.

Planning changes nothing on the machine. It looks at the catalog, the
platform and PATH, and produces one Decision per tool.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Literal

from gonk.core.errors import GonkError
from gonk.core.platform import Platform
from gonk.core.system import System
from gonk.tools.catalog import ToolSpec
from gonk.tools.detect import Detection, detect
from gonk.tools.providers import PROVIDERS, Command, Provider

Action = Literal["present", "upgrade", "install", "unavailable"]


@dataclass(frozen=True)
class Decision:
    tool: str
    action: Action
    reason: str
    detection: Detection
    provider: str = ""
    commands: tuple[Command, ...] = ()
    refresh: Command | None = None

    @property
    def changes_machine(self) -> bool:
        return self.action in ("install", "upgrade")

    @property
    def needs_root(self) -> bool:
        return any(command.needs_root for command in self.commands)


def expand(requested: Iterable[str], catalog: Mapping[str, ToolSpec]) -> list[str]:
    """Add the tools that the requested ones require, each before its dependant."""
    ordered: list[str] = []

    def visit(name: str, trail: tuple[str, ...]) -> None:
        if name in ordered:
            return
        if name in trail:
            raise GonkError(f"Tools require each other in a loop: {' → '.join([*trail, name])}.")
        spec = catalog.get(name)
        for requirement in spec.requires if spec else ():
            visit(requirement, (*trail, name))
        ordered.append(name)

    for name in requested:
        visit(name, ())
    return ordered


def decide(
    spec: ToolSpec,
    platform: Platform,
    system: System,
    planned: frozenset[str],
    providers: Mapping[str, Provider],
) -> Decision:
    found = detect(spec, system)
    if found.present and found.acceptable:
        version = f" {found.version}" if found.version else ""
        return Decision(spec.name, "present", f"already installed{version}", found)

    action: Action = "upgrade" if found.present else "install"
    for name, options in spec.install.items():
        provider = providers.get(name)
        if provider is None:
            raise GonkError(f"Tool '{spec.name}' names a provider that does not exist: '{name}'.")
        if not provider.available(platform, system, planned):
            continue
        commands = tuple(provider.commands(options, system))
        needs_root = any(command.needs_root for command in commands)
        if needs_root and not system.is_root() and system.which("sudo") is None:
            return Decision(
                spec.name,
                "unavailable",
                f"{name} needs root, and sudo is not installed",
                found,
            )
        if found.present:
            reason = f"{found.version} is older than {spec.min_version}"
        else:
            reason = f"not installed; {name} can install it"
        return Decision(spec.name, action, reason, found, name, commands, provider.refresh())

    if found.present:
        return Decision(
            spec.name,
            "present",
            f"{found.version} is older than {spec.min_version}, but nothing here can upgrade it",
            found,
        )
    tried = ", ".join(spec.install) or "none listed"
    where = platform.pretty_name or platform.os
    return Decision(spec.name, "unavailable", f"no provider for {where} (tried: {tried})", found)


def plan(
    requested: Iterable[str],
    catalog: Mapping[str, ToolSpec],
    platform: Platform,
    system: System,
    providers: Mapping[str, Provider] | None = None,
) -> list[Decision]:
    providers = PROVIDERS if providers is None else providers
    decisions: list[Decision] = []
    planned: set[str] = set()
    for name in expand(requested, catalog):
        spec = catalog.get(name)
        if spec is None:
            decisions.append(
                Decision(name, "unavailable", "not in the tool catalog", Detection(name, False))
            )
            continue
        decision = decide(spec, platform, system, frozenset(planned), providers)
        if decision.changes_machine:
            planned.update(spec.commands)
        decisions.append(decision)
    return decisions
