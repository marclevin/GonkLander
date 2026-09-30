"""Run a command the owner has named in config. Off unless explicitly allowed."""

from __future__ import annotations

from typing import Any

from gonk.capabilities.registry import CapabilityError, Context, capability

OUTPUT_LIMIT = 20_000
TIMEOUT_SECONDS = 60


def _clip(text: str) -> str:
    return text if len(text) <= OUTPUT_LIMIT else text[:OUTPUT_LIMIT] + "\n… (truncated)"


@capability(
    "commands.list",
    risk="sensitive",
    description="Names of the commands that commands.run may run.",
)
def list_commands(ctx: Context) -> list[str]:
    return sorted(ctx.config.commands)


@capability(
    "commands.run",
    risk="dangerous",
    description=(
        "Run one of the commands named in config. Takes the name only; "
        "the command line is fixed by the owner and cannot be extended."
    ),
)
def run(ctx: Context, name: str) -> dict[str, Any]:
    argv = ctx.config.commands.get(name)
    if argv is None:
        raise CapabilityError(
            f"There is no command called '{name}'.",
            hints=["Commands are defined under 'commands:' in config.yaml."],
        )
    result = ctx.system.run(argv, timeout=TIMEOUT_SECONDS)
    return {
        "name": name,
        "exit_code": result.returncode,
        "stdout": _clip(result.stdout),
        "stderr": _clip(result.stderr),
    }
