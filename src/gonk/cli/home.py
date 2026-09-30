"""gonk home"""

from __future__ import annotations

import sys
from typing import Annotated

import typer

from gonk.cli import common
from gonk.cli.common import out
from gonk.core import ui
from gonk.core.config import Config, save_home_token
from gonk.core.errors import GonkError, hints_from
from gonk.core.system import System
from gonk.home import ssh
from gonk.home import status as home_status
from gonk.home.client import AgentClient, AgentRefused, AgentUnreachable

app = common.group("Reach your home machine.")


def reachable_address(config: Config, system: System) -> str:
    """The address to dial, or a GonkError that shows which layer is broken."""
    name = config.home.name
    ssh.validate(config)
    report = home_status.inspect(config, system)
    if report.is_here:
        raise GonkError(f"You are already on {name}.")
    if not report.network_ok:
        raise GonkError(
            f"Could not reach {name}.",
            checks=report.checks,
            hints=hints_from(report.checks),
        )
    return report.address


@app.callback()
def default(context: typer.Context) -> None:
    if context.invoked_subcommand is None:
        status()


@app.command()
def status() -> None:
    """Check the connection to home, one layer at a time."""
    system, config = common.load()
    ssh.validate(config)
    name = config.home.name
    report = home_status.inspect(config, system)

    if report.is_here:
        out.print(f"[bold]This machine is {name}.[/bold] You are home.")
        return
    if not report.reachable:
        raise GonkError(
            f"Could not reach {name}.", checks=report.checks, hints=hints_from(report.checks)
        )
    out.print(f"[bold]{name} is reachable[/bold] over {config.home.transport}.\n")
    ui.print_checks(report.checks)


@app.command()
def shell(
    no_tmux: Annotated[
        bool, typer.Option("--no-tmux", help="A plain login shell, without tmux.")
    ] = False,
) -> None:
    """Open a shell on home. Reattaches to the same tmux session each time."""
    system, config = common.load()
    if system.which("ssh") is None:
        raise GonkError("ssh is not installed here.", hints=["gonk tools install ssh"])
    address = reachable_address(config, system)
    argv = ssh.shell_command(config, address, tmux=not no_tmux)
    common.audit(config).record_quietly("home.shell", host=address)
    system.replace_process(argv)


@app.command()
def code(
    path: Annotated[
        str, typer.Argument(help="Folder on home to open. Defaults to home.code_path.")
    ] = "",
) -> None:
    """Open VS Code here, working on files at home."""
    system, config = common.load()
    if system.which("code") is None:
        raise GonkError(
            "VS Code is not installed here (no 'code' command).",
            hints=[
                "Install VS Code and its Remote - SSH extension: https://code.visualstudio.com",
                "gonk home shell    # works in any terminal",
            ],
        )
    address = reachable_address(config, system)
    argv = ssh.code_command(config, address, path)
    common.audit(config).record_quietly("home.code", host=address)
    result = system.run(argv, timeout=30)
    if not result.ok:
        raise GonkError(
            f"VS Code did not start: {result.text}",
            hints=["code --install-extension ms-vscode-remote.remote-ssh"],
        )
    out.print(f"[green]✓[/green] Opening VS Code on {config.home.name}.")


@app.command()
def pair(
    token_stdin: Annotated[
        bool, typer.Option("--token-stdin", help="Read the token from standard input.")
    ] = False,
) -> None:
    """Store this device's token for the agent at home."""
    system, config = common.load()
    name = config.home.name
    if token_stdin:
        token = sys.stdin.readline().strip()
    else:
        out.print(f"On {name}, run:  [bold]gonk agent token create <this-device>[/bold]\n")
        token = typer.prompt("Token", hide_input=True).strip()
    if not token.startswith("gonk_"):
        raise GonkError("That does not look like a Gonk token; they start with 'gonk_'.")

    # Check it if home can be reached; store it either way, so that pairing
    # works on a machine that is not on the network yet.
    report = home_status.inspect(config, system)
    verified = ""
    if report.address and report.network_ok:
        client = AgentClient(report.address, config.home.agent_port, token, name=name, timeout=5)
        try:
            verified = str(client.whoami().get("device", ""))
        except AgentRefused as error:
            raise GonkError(
                f"{name} rejected that token. Nothing was stored.", hints=error.hints
            ) from None
        except AgentUnreachable:
            pass
        finally:
            client.close()

    path = save_home_token(config, token)
    common.audit(config).record_quietly("home.pair", verified=bool(verified))
    if verified:
        out.print(f"[green]✓[/green] Paired with {name} as '{verified}'.")
    else:
        out.print(f"[green]✓[/green] Token stored. {name} could not be reached to check it.")
    out.print(f"[dim]Stored in {path} (readable only by you).[/dim]")
