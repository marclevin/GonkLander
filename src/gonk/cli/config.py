"""gonk config"""

from __future__ import annotations

from importlib import resources
from typing import Annotated

import typer
import yaml

from gonk.cli import common
from gonk.cli.common import out
from gonk.core import config as configuration
from gonk.core import paths
from gonk.core.errors import GonkError

app = common.group("See and change Gonk's configuration.")


@app.callback()
def default(context: typer.Context) -> None:
    if context.invoked_subcommand is None:
        show()


@app.command()
def path() -> None:
    """Where the configuration file lives."""
    _system, config = common.load()
    print(config.path)


@app.command()
def show() -> None:
    """The settings in effect: defaults, file and environment combined."""
    _system, config = common.load()
    source = str(config.path) if config.exists else "defaults (no config file yet)"
    out.print(f"[dim]# {source}[/dim]")
    out.print(yaml.safe_dump(config.settings(), sort_keys=False).rstrip(), markup=False)
    for warning in config.warnings:
        out.print(f"[yellow]![/yellow] {warning}")


@app.command()
def init() -> None:
    """Create an annotated config file, if there is not one already."""
    _system, config = common.load()
    if config.exists:
        out.print(f"{config.path} already exists; leaving it alone.")
        return
    template = resources.files("gonk.data").joinpath("config.example.yaml")
    paths.ensure_directory(config.directory)
    config.path.write_text(template.read_text(encoding="utf-8"), encoding="utf-8")
    out.print(f"[green]✓[/green] Wrote {config.path}")


@app.command("set")
def set_value(
    key: Annotated[str, typer.Argument(help="Setting, as section.name: home.host")],
    value: Annotated[str, typer.Argument(help="New value. Lists are comma-separated.")],
) -> None:
    """Change one setting."""
    system, config = common.load()
    if key.startswith("commands"):
        raise GonkError(
            "Commands are edited in the file, not set from the command line.",
            hints=[f"Open {config.path} and add them under 'commands:'."],
        )
    stored = configuration.set_value(config.directory, key, value)
    common.audit(config).record_quietly("config.set", key=key, value=stored)
    out.print(f"[green]✓[/green] {key} = {stored!r}")
    variable = f"GONK_{key.replace('.', '_')}".upper()
    if variable in system.env:
        out.print(f"[yellow]![/yellow] {variable} is set in your environment and takes precedence.")
