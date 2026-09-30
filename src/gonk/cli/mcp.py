"""gonk mcp"""

from __future__ import annotations

import json
from typing import Annotated

import anyio
import typer
from rich.markup import escape
from rich.table import Table

from gonk.capabilities.invoke import build_gate
from gonk.capabilities.registry import Context
from gonk.cli import common
from gonk.cli.common import out
from gonk.core import ui
from gonk.core.config import Config, home_token
from gonk.core.errors import GonkError, hints_from
from gonk.core.system import System
from gonk.home import status as home_status
from gonk.mcp.backends import Backend, HomeBackend, LocalBackend

app = common.group("Serve Gonk's tools to AI clients over MCP.")

Home = Annotated[
    bool, typer.Option("--home", help="Use the tools on your home machine, through its agent.")
]

RISK_STYLE = {"safe": "green", "sensitive": "yellow", "dangerous": "red"}


def backend_for(config: Config, system: System, *, home: bool) -> Backend:
    if not home:
        gate = build_gate(config, "mcp")
        return LocalBackend(gate, Context(config=config, system=system, caller="mcp"))

    name = config.home.name
    if not home_token(config, system.env):
        raise GonkError(
            f"This device has no token for {name}.",
            hints=[f"On {name}: gonk agent token create <this-device>", "Here: gonk home pair"],
        )
    report = home_status.inspect(config, system)
    if not report.network_ok:
        raise GonkError(
            f"Could not reach {name}.", checks=report.checks, hints=hints_from(report.checks)
        )
    return HomeBackend(home_status.client_for(config, system, report.address))


@app.callback()
def default(context: typer.Context) -> None:
    if context.invoked_subcommand is None:
        status()


@app.command("list")
def list_tools(home: Home = False) -> None:
    """The tools, their risk, and whether policy allows each one."""
    system, config = common.load()
    table = Table(box=None, pad_edge=False, padding=(0, 2))
    table.add_column("")
    table.add_column("Capability", style="bold")
    table.add_column("Risk")
    table.add_column("MCP tool", style="dim")

    def row(allowed: bool, name: str, risk: str, tool_name: str) -> None:
        style = RISK_STYLE.get(risk, "white")
        mark = "[green]✓[/green]" if allowed else "[dim]·[/dim]"
        table.add_row(mark, name, f"[{style}]{risk}[/{style}]", tool_name if allowed else "")

    if home:
        tools = backend_for(config, system, home=True).tools()
        for tool in tools:
            row(True, tool.capability, tool.risk, tool.tool_name)
        out.print(table)
        out.print(f"\n{len(tools)} tools available from {config.home.name}.")
        out.print(f"[dim]What is allowed is decided by the config on {config.home.name}.[/dim]")
        return

    gate = build_gate(config, "mcp")
    verdicts = {item.name: gate.policy.check(item) for item in gate.registry.all()}
    for item in gate.registry.all():
        row(verdicts[item.name].allowed, item.name, item.risk, item.tool_name)
    out.print(table)

    switched_on = sum(verdict.allowed for verdict in verdicts.values())
    out.print(f"\n{switched_on} of {len(verdicts)} on. Safe tools are always on.")
    off = [item for item in gate.registry.all() if not verdicts[item.name].allowed]
    if off:
        out.print("\nWhy the others are off:")
        for item in off:
            out.print(f"  {item.name}  [dim]{escape(verdicts[item.name].reason)}[/dim]")
        out.print("\n[dim]gonk config set policy.allow <name>,<name>    switches tools on[/dim]")
    for problem in gate.registry.problems:
        out.print(f"[red]✗[/red] plugin {escape(problem)}")


@app.command()
def status() -> None:
    """Is MCP switched on, and what would it serve?"""
    from gonk.doctor import mcp_section

    system, config = common.load()
    ui.print_checks(mcp_section(config, system).checks)
    calls = [
        entry
        for entry in common.audit(config).tail(200)
        if str(entry.get("event", "")).startswith("capability.")
    ][-5:]
    if calls:
        out.print("\nRecent calls:")
        for entry in calls:
            out.print(
                f"  [dim]{entry.get('time', '')}[/dim]  {entry.get('event', '')}  "
                f"{entry.get('capability', '')}  [dim]{entry.get('caller', '')}[/dim]"
            )
    out.print("\n[dim]gonk mcp list       every tool and why it is or is not allowed[/dim]")
    out.print("[dim]gonk mcp install    how to connect an AI client[/dim]")


@app.command()
def install(
    home: Home = False,
    apply: Annotated[
        bool, typer.Option("--apply", help="Register with Claude Code now, using its CLI.")
    ] = False,
) -> None:
    """Show how to connect Claude Code or Claude Desktop to Gonk."""
    from gonk.agent.service import gonk_executable

    system, config = common.load()
    arguments = ["mcp", "serve", *(["--home"] if home else [])]
    executable = gonk_executable(system)
    name = "gonk-home" if home else "gonk"

    if apply:
        if system.which("claude") is None:
            raise GonkError(
                "Claude Code is not installed here.", hints=["gonk tools install claude-code"]
            )
        argv = ["claude", "mcp", "add", "--scope", "user", name, "--", executable, *arguments]
        result = system.run(argv, timeout=30)
        if not result.ok:
            raise GonkError(f"Claude Code refused: {result.text}")
        common.audit(config).record_quietly("mcp.install", client="claude-code", name=name)
        out.print(f"[green]✓[/green] Registered '{name}' with Claude Code.")
        return

    out.print("[bold]Claude Code[/bold]\n")
    print(f"    claude mcp add --scope user {name} -- {executable} {' '.join(arguments)}")
    out.print("\n    [dim]or let Gonk do it:[/dim] gonk mcp install --apply\n")
    out.print("[bold]Claude Desktop[/bold]  [dim](Settings → Developer → Edit Config)[/dim]\n")
    snippet = {"mcpServers": {name: {"command": executable, "args": arguments}}}
    print(json.dumps(snippet, indent=2))
    out.print(
        "\n[bold]ChatGPT[/bold]\n\n"
        "    ChatGPT connects to MCP servers over the network, not as a local program.\n"
        "    Gonk does not offer a network MCP endpoint yet; see docs/security.md."
    )


@app.command()
def serve(home: Home = False) -> None:
    """Run the MCP server on standard input and output."""
    from gonk.mcp.server import serve_stdio

    system, config = common.load()
    if not config.mcp.enabled:
        raise GonkError(
            "MCP is switched off in config.", hints=["gonk config set mcp.enabled true"]
        )
    backend = backend_for(config, system, home=home)
    # Standard output belongs to the protocol from here on; say nothing on it.
    common.audit(config).record_quietly("mcp.start", backend="home" if home else "local")
    anyio.run(serve_stdio, backend)
