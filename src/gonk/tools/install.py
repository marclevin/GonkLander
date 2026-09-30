"""Carry out a plan."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from gonk.core.system import System
from gonk.tools.catalog import ToolSpec
from gonk.tools.detect import detect
from gonk.tools.planner import Decision
from gonk.tools.providers import Command


@dataclass(frozen=True)
class Outcome:
    tool: str
    ok: bool
    message: str
    note: str = ""


def _run(command: Command, system: System) -> int:
    as_root = system.is_root()
    # With sudo in front, the environment travels inside argv (sudo env K=V ...).
    env = dict(command.env) if (as_root or not command.needs_root) else None
    return system.run_interactive(command.full_argv(as_root), env=env)


def execute(
    decisions: Iterable[Decision],
    catalog: Mapping[str, ToolSpec],
    system: System,
    announce: Callable[[str], None] = lambda message: None,
) -> list[Outcome]:
    """Run the commands of every decision that changes the machine.

    Success means the tool can be found afterwards, not that a command exited 0.
    """
    outcomes: list[Outcome] = []
    failed: set[str] = set()
    refreshed: set[str] = set()

    for decision in decisions:
        if not decision.changes_machine:
            continue
        spec = catalog[decision.tool]

        if blocked := [name for name in spec.requires if name in failed]:
            failed.add(spec.name)
            outcomes.append(Outcome(spec.name, False, f"skipped: {blocked[0]} did not install"))
            continue

        if decision.refresh is not None and decision.provider not in refreshed:
            refreshed.add(decision.provider)
            announce(decision.refresh.display(system.is_root()))
            _run(decision.refresh, system)  # stale indexes are not fatal; the install will tell

        code = 0
        for command in decision.commands:
            announce(command.display(system.is_root()))
            code = _run(command, system)
            if code != 0:
                break

        found = detect(spec, system)
        if code != 0:
            failed.add(spec.name)
            outcomes.append(Outcome(spec.name, False, f"{decision.provider} exited with {code}"))
        elif not found.present:
            failed.add(spec.name)
            wanted = " or ".join(spec.commands)
            outcomes.append(
                Outcome(spec.name, False, f"installer finished, but '{wanted}' is not on PATH")
            )
        elif not found.acceptable:
            outcomes.append(
                Outcome(
                    spec.name,
                    True,
                    f"installed {found.version}; {decision.provider} has nothing newer "
                    f"(wanted {spec.min_version}+)",
                    spec.note,
                )
            )
        else:
            version = f" {found.version}" if found.version else ""
            outcomes.append(Outcome(spec.name, True, f"installed{version}", spec.note))
    return outcomes
