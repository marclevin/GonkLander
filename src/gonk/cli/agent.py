"""gonk agent"""

from __future__ import annotations

from typing import Annotated

import typer
from rich.table import Table

from gonk.agent import service
from gonk.agent.bind import resolve_bind
from gonk.agent.tokens import DeviceStore
from gonk.cli import common
from gonk.cli.common import out
from gonk.core.errors import GonkError

app = common.group("Run the Gonk Agent on a machine you own.")
token_app = common.group("Manage the devices allowed to connect.")
app.add_typer(token_app, name="token")


@app.callback()
def default(context: typer.Context) -> None:
    if context.invoked_subcommand is None:
        status()


@app.command()
def run() -> None:
    """Run the agent in the foreground. This is what the service runs."""
    system, config = common.load()
    service.run(config, system)


@app.command()
def install() -> None:
    """Install the agent as a systemd user service."""
    system, config = common.load()
    resolve_bind(config, system)  # fail now, with a clear message, rather than in the journal
    path = service.install(system)
    common.audit(config).record_quietly("agent.install", unit=str(path))
    out.print(f"[green]✓[/green] Installed {path}")
    out.print("\nNext:")
    if not DeviceStore(config.directory).devices():
        out.print("    gonk agent token create <device-name>")
    out.print("    gonk agent start")
    out.print(
        "\n[dim]To keep the agent running while you are logged out:[/dim]\n"
        "    sudo loginctl enable-linger $USER"
    )


@app.command()
def uninstall() -> None:
    """Stop the agent and remove its service."""
    system, config = common.load()
    service.uninstall(system)
    common.audit(config).record_quietly("agent.uninstall")
    out.print("[green]✓[/green] The agent service is removed. Device tokens were kept.")


@app.command()
def start() -> None:
    """Start the agent service."""
    system, config = common.load()
    if service.state(system) == "not-installed":
        raise GonkError("The agent service is not installed.", hints=["gonk agent install"])
    if not DeviceStore(config.directory).devices():
        raise GonkError(
            "No devices are allowed to connect yet, so the agent would have nothing to do.",
            hints=["gonk agent token create <device-name>"],
        )
    host = resolve_bind(config, system)
    # restart, not start: picks up config changes if it was already running.
    service.require(service.systemctl(system, "restart", service.UNIT_NAME), "start the agent")
    out.print(f"[green]✓[/green] The agent is running on {host}:{config.agent.port}.")


@app.command()
def stop() -> None:
    """Stop the agent service."""
    system, _config = common.load()
    if service.state(system) == "not-installed":
        out.print("The agent service is not installed; nothing to stop.")
        return
    service.require(service.systemctl(system, "stop", service.UNIT_NAME), "stop the agent")
    out.print("[green]✓[/green] The agent is stopped.")


@app.command()
def status() -> None:
    """Is the agent running, and who may connect?"""
    from gonk.core import ui
    from gonk.doctor import agent_section

    system, config = common.load()
    ui.print_checks(agent_section(config, system).checks)


@token_app.callback()
def token_default(context: typer.Context) -> None:
    if context.invoked_subcommand is None:
        token_list()


@token_app.command("create")
def token_create(
    device: Annotated[str, typer.Argument(help="A name for the device: work-laptop")],
) -> None:
    """Allow a new device to connect. Prints its token once."""
    _system, config = common.load()
    token = DeviceStore(config.directory).create(device)
    common.audit(config).record_quietly("agent.token.created", device=device)
    out.print(f"[green]✓[/green] '{device}' may now connect.\n")
    out.print("Its token, shown this once:\n")
    print(f"    {token}\n")
    out.print(f"On {device}, run:  [bold]gonk home pair[/bold]")


@token_app.command("list")
def token_list() -> None:
    """Devices that may connect."""
    _system, config = common.load()
    devices = DeviceStore(config.directory).devices()
    if not devices:
        out.print("No devices yet.  [dim]gonk agent token create <device-name>[/dim]")
        return
    table = Table(box=None, pad_edge=False, padding=(0, 2))
    table.add_column("Device", style="bold")
    table.add_column("Created", style="dim")
    for device in devices:
        table.add_row(device.name, device.created)
    out.print(table)


@token_app.command("revoke")
def token_revoke(
    device: Annotated[str, typer.Argument(help="The device to cut off.")],
) -> None:
    """Stop a device from connecting. Takes effect immediately."""
    _system, config = common.load()
    DeviceStore(config.directory).revoke(device)
    common.audit(config).record_quietly("agent.token.revoked", device=device)
    out.print(f"[green]✓[/green] '{device}' can no longer connect.")
