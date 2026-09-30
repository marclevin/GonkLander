"""Terminal output. Everything the user sees goes through here."""

from __future__ import annotations

from collections.abc import Iterable

from rich.console import Console
from rich.markup import escape

from gonk.core.errors import Check, GonkError

out = Console(highlight=False)
err = Console(stderr=True, highlight=False)

SYMBOLS = {
    "ok": "[green]✓[/green]",
    "warn": "[yellow]![/yellow]",
    "fail": "[red]✗[/red]",
    "info": "[dim]·[/dim]",
}


def check_line(check: Check) -> str:
    line = f"  {SYMBOLS[check.status]} {escape(check.name)}"
    if check.detail:
        line += f"  [dim]{escape(check.detail)}[/dim]"
    return line


def say(console: Console, text: str) -> None:
    """Print without folding at the terminal width. Messages often contain
    paths and commands, and a wrapped path cannot be copied or clicked."""
    console.print(text, soft_wrap=True)


def print_checks(checks: Iterable[Check], console: Console = out, *, hints: bool = True) -> None:
    for check in checks:
        say(console, check_line(check))
        if hints and check.hint and check.status in ("warn", "fail"):
            say(console, f"      [dim]→[/dim] {escape(check.hint)}")


def heading(text: str, console: Console = out) -> None:
    console.print(f"\n[bold]{escape(text)}[/bold]")


def print_error(error: GonkError) -> None:
    say(err, f"[bold red]{escape(error.message)}[/bold red]")
    if error.checks:
        err.print()
        print_checks(error.checks, err, hints=False)
    if error.hints:
        err.print("\nTry:")
        for hint in error.hints:
            say(err, f"    {escape(hint)}")
