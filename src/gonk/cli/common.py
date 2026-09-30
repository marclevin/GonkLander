"""Helpers shared by the command modules."""

from __future__ import annotations

import sys
from typing import Any

import typer

from gonk.capabilities.invoke import audit_log
from gonk.core import config as configuration
from gonk.core import ui
from gonk.core.audit import AuditLog
from gonk.core.config import Config
from gonk.core.errors import GonkError
from gonk.core.system import System


def group(help_text: str) -> typer.Typer:
    """A command group that does something useful when called on its own."""
    return typer.Typer(
        help=help_text,
        invoke_without_command=True,
        no_args_is_help=False,
        add_completion=False,
        pretty_exceptions_enable=False,
        rich_markup_mode=None,
    )


def load() -> tuple[System, Config]:
    system = System()
    return system, configuration.load(system)


def audit(config: Config) -> AuditLog:
    return audit_log(config)


def confirm(question: str, *, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        raise GonkError(
            "This needs a yes or no, and there is no terminal to ask on.",
            hints=["Add --yes to go ahead without asking."],
        )
    return typer.confirm(question, default=True)


def print_json(data: Any) -> None:
    import json

    # Plain print: rich would wrap long lines and break the JSON.
    print(json.dumps(data, indent=2, default=str))


out = ui.out
