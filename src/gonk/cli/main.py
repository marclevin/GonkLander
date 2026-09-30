"""The `gonk` command."""

from __future__ import annotations

import os
import sys
from typing import Annotated

import typer
from rich.markup import escape

from gonk import __version__, doctor
from gonk.agent import service
from gonk.cli import agent, common, home, mcp, tools
from gonk.cli import config as config_commands
from gonk.cli.common import out
from gonk.core import config as configuration
from gonk.core import platform as platforms
from gonk.core import state, ui
from gonk.core.config import Config
from gonk.core.errors import Check, GonkError, Status
from gonk.core.system import System
from gonk.home import status as home_status

PACKAGE = "gonklander"

app = common.group("Gonk: land on a machine and make it yours.")
app.add_typer(tools.app, name="tools")
app.add_typer(config_commands.app, name="config")
app.add_typer(home.app, name="home")
app.add_typer(agent.app, name="agent")
app.add_typer(mcp.app, name="mcp")
app.command("land")(tools.land)
app.command("profiles")(tools.profiles)


def show_version(requested: bool) -> None:
    if requested:
        print(f"gonk {__version__}")
        raise typer.Exit()


@app.callback()
def root(
    context: typer.Context,
    version: Annotated[
        bool,
        typer.Option("--version", callback=show_version, is_eager=True, help="Show the version."),
    ] = False,
) -> None:
    if context.invoked_subcommand is None:
        status()
        out.print(
            "\n[dim]gonk doctor     full report        gonk land      install tools\n"
            "gonk home       reach home         gonk --help    everything else[/dim]"
        )


def load_leniently(system: System) -> tuple[Config, str]:
    """For the commands that must work even when the config file is broken."""
    try:
        return configuration.load(system), ""
    except GonkError as error:
        from gonk.core import paths

        fallback = Config()
        fallback.directory = paths.config_dir(system.env, system.home())
        fallback.state_directory = paths.state_dir(system.env, system.home())
        return fallback, error.message


@app.command()
def status() -> None:
    """A one-screen summary of this machine."""
    system = System()
    config, config_error = load_leniently(system)
    machine = platforms.detect(system)

    out.print(f"[bold]gonk {__version__}[/bold] on [bold]{escape(system.hostname())}[/bold]")
    out.print(f"[dim]{escape(machine.pretty_name)} · {machine.arch}[/dim]\n")

    checks = []
    if config_error:
        checks.append(Check("Configuration", "fail", config_error))
    gonk = doctor.gonk_section(config, system, "")
    checks.extend(check for check in gonk.checks if check.name.startswith("Profile"))
    landed = state.load(config.state_directory).get("profile")
    if landed:
        checks.append(Check("Last landed", "info", str(landed)))

    report = home_status.inspect(config, system)
    if report.is_here:
        checks.append(Check("Home", "ok", f"this machine is {config.home.name}"))
    elif report.reachable:
        checks.append(Check("Home", "ok", f"{config.home.name} is reachable"))
    else:
        broken = next((check for check in report.checks if check.status != "ok"), None)
        detail = f"{broken.name}: {broken.detail}" if broken else "not reachable"
        severity: Status = "fail" if broken and broken.status == "fail" else "warn"
        checks.append(Check("Home", severity, detail, "gonk home status"))

    agent_state = service.state(system)
    if agent_state != "not-installed" or report.is_here:
        checks.extend(doctor.agent_section(config, system).checks[:1])
    checks.extend(doctor.mcp_section(config, system).checks[:1])
    ui.print_checks(checks)


@app.command("doctor")
def run_doctor(
    as_json: Annotated[bool, typer.Option("--json", help="Machine-readable output.")] = False,
) -> None:
    """Inspect this machine and report what is missing or broken."""
    system = System()
    config, config_error = load_leniently(system)
    report = doctor.examine(config, system, config_error)

    if as_json:
        common.print_json(report.as_data())
    else:
        out.print(f"[bold]Gonk doctor[/bold]  [dim]gonk {__version__}[/dim]")
        for section in report.sections:
            ui.heading(section.title)
            ui.print_checks(section.checks)
        failures, warnings = report.count("fail"), report.count("warn")
        out.print()
        if failures:
            out.print(f"[red]{failures} problem(s)[/red], {warnings} warning(s).")
        elif warnings:
            out.print(f"[green]No problems.[/green] {warnings} warning(s), each with a suggestion.")
        else:
            out.print("[green]Everything looks good.[/green]")
    if not report.healthy:
        raise typer.Exit(1)


@app.command()
def update() -> None:
    """Upgrade gonk itself."""
    system = System()
    if system.which("uv") is None:
        raise GonkError(
            "gonk is installed with uv, and uv is not on PATH.",
            hints=["Run the installer again: curl -fsSL https://marclevin.me/gonk | bash"],
        )
    listed = system.run(["uv", "tool", "list"], timeout=30)
    if PACKAGE not in listed.stdout:
        raise GonkError(
            "This gonk was not installed as a uv tool, so it cannot upgrade itself.",
            hints=[
                "From a checkout: git pull && ./lander/install.sh",
                "Otherwise:       curl -fsSL https://marclevin.me/gonk | bash",
            ],
        )
    out.print(f"Upgrading from {__version__}…")
    code = system.run_interactive(["uv", "tool", "upgrade", PACKAGE, "--reinstall"])
    if code != 0:
        raise GonkError(
            "The upgrade did not finish.",
            hints=["Run the installer again; it repairs a broken install."],
        )
    now = system.run(["gonk", "--version"], timeout=30).text or "gonk"
    out.print(f"[green]✓[/green] Now at {escape(now)}.")


@app.command()
def log(
    count: Annotated[int, typer.Option("-n", help="How many entries to show.")] = 20,
) -> None:
    """Recent entries from the audit log."""
    system = System()
    config, _error = load_leniently(system)
    audit = common.audit(config)
    entries = audit.tail(count)
    if not entries:
        out.print(f"Nothing recorded yet.  [dim]{audit.path}[/dim]")
        return
    for entry in entries:
        rest = {key: value for key, value in entry.items() if key not in ("time", "event")}
        details = "  ".join(f"{key}={value}" for key, value in rest.items())
        out.print(
            f"[dim]{entry.get('time', '')}[/dim]  [bold]{entry.get('event', '')}[/bold]  "
            f"{escape(details)}"
        )
    out.print(f"\n[dim]{audit.path}[/dim]")


def main() -> None:
    try:
        app()
    except GonkError as error:
        ui.print_error(error)
        if os.environ.get("GONK_DEBUG"):
            raise
        sys.exit(1)
    except KeyboardInterrupt:
        ui.err.print("\nStopped.")
        sys.exit(130)


if __name__ == "__main__":
    main()
