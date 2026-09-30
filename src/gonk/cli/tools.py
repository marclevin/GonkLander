"""gonk land, gonk tools, gonk profiles."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

import typer
from rich.markup import escape
from rich.table import Table

from gonk.cli import common
from gonk.cli.common import out
from gonk.core import platform as platforms
from gonk.core import state
from gonk.core.config import Config
from gonk.core.errors import GonkError
from gonk.core.system import System
from gonk.tools.catalog import load_catalog
from gonk.tools.detect import detect
from gonk.tools.install import execute
from gonk.tools.planner import Decision, plan
from gonk.tools.profiles import load_profiles, resolve_tools

app = common.group("See and install individual tools.")

DryRun = Annotated[bool, typer.Option("--dry-run", help="Show the plan and change nothing.")]
Yes = Annotated[bool, typer.Option("--yes", "-y", help="Do not ask before installing.")]

ACTION_STYLE = {
    "present": "[green]✓ present[/green]",
    "install": "[cyan]+ install[/cyan]",
    "upgrade": "[cyan]↑ upgrade[/cyan]",
    "unavailable": "[red]✗ unavailable[/red]",
}


def show_plan(decisions: list[Decision]) -> None:
    table = Table(box=None, pad_edge=False, padding=(0, 2))
    table.add_column("Tool", style="bold")
    table.add_column("Plan")
    table.add_column("Why", style="dim")
    for decision in decisions:
        table.add_row(decision.tool, ACTION_STYLE[decision.action], escape(decision.reason))
    out.print(table)


def carry_out(
    requested: list[str],
    label: str,
    system: System,
    config: Config,
    *,
    dry_run: bool,
    assume_yes: bool,
) -> bool:
    """Plan, show, confirm, install, report. Returns True if everything wanted is present."""
    machine = platforms.detect(system)
    catalog = load_catalog(config.directory)
    decisions = plan(requested, catalog, machine, system)

    out.print(f"\n[bold]{escape(label)}[/bold] on {escape(machine.pretty_name)} ({machine.arch})\n")
    show_plan(decisions)

    changes = [decision for decision in decisions if decision.changes_machine]
    missing = [decision for decision in decisions if decision.action == "unavailable"]

    if not changes:
        if missing:
            out.print(f"\nNothing Gonk can install here. {len(missing)} tool(s) are unavailable.")
            return False
        out.print("\n[green]Nothing to do.[/green] Everything is already here.")
        return True

    as_root = system.is_root()
    out.print("\nCommands:")
    shown: set[str] = set()
    for decision in changes:
        steps = [decision.refresh, *decision.commands] if decision.refresh else decision.commands
        for command in steps:
            text = command.display(as_root)
            if text not in shown:
                shown.add(text)
                out.print(f"    {escape(text)}", soft_wrap=True)

    if dry_run:
        out.print("\n[dim]Dry run: nothing was changed.[/dim]")
        return False

    needs_root = any(decision.needs_root for decision in changes) and not as_root
    if needs_root:
        out.print("\n[dim]Some of these use sudo, which may ask for your password.[/dim]")
    out.print()
    if not common.confirm(f"Install {len(changes)} tool(s)?", assume_yes=assume_yes):
        out.print("Nothing was changed.")
        return False

    outcomes = execute(
        changes,
        catalog,
        system,
        announce=lambda text: out.print(f"\n[bold]$ {escape(text)}[/bold]"),
    )

    out.print()
    log = common.audit(config)
    for outcome in outcomes:
        symbol = "[green]✓[/green]" if outcome.ok else "[red]✗[/red]"
        out.print(f"  {symbol} {outcome.tool}  [dim]{escape(outcome.message)}[/dim]")
        log.record_quietly("tool.install", tool=outcome.tool, ok=outcome.ok, detail=outcome.message)
    notes = [(outcome.tool, outcome.note) for outcome in outcomes if outcome.ok and outcome.note]
    if notes:
        out.print("\nNext steps:")
        for tool, note in notes:
            out.print(f"  [bold]{tool}[/bold]: {escape(note)}")

    failures = [outcome for outcome in outcomes if not outcome.ok]
    if failures:
        out.print(
            f"\n[yellow]{len(failures)} tool(s) did not install.[/yellow] "
            "Running the same command again is safe; it will only retry those."
        )
    return not failures and not missing


def land(
    profile: Annotated[
        str, typer.Argument(help="Profile to install. Defaults to lander.default_profile.")
    ] = "",
    dry_run: DryRun = False,
    yes: Yes = False,
) -> None:
    """Install a profile of tools. Skips whatever is already here."""
    system, config = common.load()
    name = profile or config.lander.default_profile
    requested = resolve_tools(name, load_profiles(config.directory))
    complete = carry_out(
        requested, f"Landing profile '{name}'", system, config, dry_run=dry_run, assume_yes=yes
    )
    if not dry_run:
        state.update(
            config.state_directory,
            profile=name,
            landed_at=datetime.now(UTC).isoformat(timespec="seconds"),
        )
    if not complete and not dry_run:
        raise typer.Exit(1)


def profiles() -> None:
    """List the profiles `gonk land` can install."""
    _system, config = common.load()
    loaded = load_profiles(config.directory)
    table = Table(box=None, pad_edge=False, padding=(0, 2))
    table.add_column("Profile", style="bold")
    table.add_column("Tools", justify="right")
    table.add_column("Description")
    table.add_column("", style="dim")
    for name in sorted(loaded, key=lambda item: len(resolve_tools(item, loaded))):
        default = "default" if name == config.lander.default_profile else ""
        table.add_row(
            name, str(len(resolve_tools(name, loaded))), loaded[name].description, default
        )
    out.print(table)
    out.print("\n[dim]gonk land <profile> --dry-run    shows exactly what would happen[/dim]")


@app.callback()
def default(context: typer.Context) -> None:
    if context.invoked_subcommand is None:
        list_tools()


@app.command("list")
def list_tools(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Every tool Gonk knows, and whether it is installed."""
    system, config = common.load()
    catalog = load_catalog(config.directory)
    found = {name: detect(spec, system) for name, spec in catalog.items()}

    if as_json:
        common.print_json(
            [
                {
                    "name": name,
                    "installed": found[name].present,
                    "version": found[name].version,
                    "path": found[name].path,
                }
                for name in catalog
            ]
        )
        return

    table = Table(box=None, pad_edge=False, padding=(0, 2))
    table.add_column("")
    table.add_column("Tool", style="bold")
    table.add_column("Version")
    table.add_column("Description", style="dim")
    for name, spec in catalog.items():
        detection = found[name]
        if not detection.present:
            symbol, version = "[dim]·[/dim]", "[dim]not installed[/dim]"
        elif not detection.acceptable:
            symbol, version = "[yellow]![/yellow]", f"{detection.version} (old)"
        else:
            symbol, version = "[green]✓[/green]", detection.version or "installed"
        table.add_row(symbol, name, version, spec.description)
    out.print(table)
    present = sum(1 for detection in found.values() if detection.present)
    out.print(f"\n{present} of {len(catalog)} installed.  [dim]gonk tools install <tool>[/dim]")


@app.command("install")
def install(
    tools: Annotated[list[str], typer.Argument(help="One or more tool names.")],
    dry_run: DryRun = False,
    yes: Yes = False,
) -> None:
    """Install tools by name."""
    system, config = common.load()
    catalog = load_catalog(config.directory)
    for name in tools:
        if name not in catalog:
            close = [known for known in catalog if name in known or known in name]
            hints = [f"Did you mean: {', '.join(close)}"] if close else []
            raise GonkError(
                f"Gonk does not know a tool called '{name}'.",
                hints=[*hints, "gonk tools list"],
            )
    label = f"Installing {', '.join(tools)}"
    complete = carry_out(tools, label, system, config, dry_run=dry_run, assume_yes=yes)
    if not complete and not dry_run:
        raise typer.Exit(1)
