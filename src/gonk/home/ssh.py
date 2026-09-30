"""Build ssh and VS Code command lines for the home machine."""

from __future__ import annotations

import re

from gonk.core.config import Config
from gonk.core.errors import GonkError

# No leading dash: a host called "-oProxyCommand=..." would be read by ssh as an option.
HOST = re.compile(r"^[A-Za-z0-9\[][A-Za-z0-9._:\[\]-]*$")
USER = re.compile(r"^[A-Za-z_][A-Za-z0-9._-]*$")
SESSION = re.compile(r"^[A-Za-z0-9_-]+$")


def validate(config: Config) -> None:
    home = config.home
    if not HOST.match(home.host):
        raise GonkError(
            f"home.host is '{home.host}', which is not a hostname or address.",
            hints=["gonk config set home.host gonksystem"],
        )
    if home.user and not USER.match(home.user):
        raise GonkError(f"home.user is '{home.user}', which is not a user name.")
    if not SESSION.match(home.tmux_session):
        raise GonkError(
            f"home.tmux_session is '{home.tmux_session}'. Use letters, digits, - and _ only."
        )
    if not 0 < home.ssh_port < 65536:
        raise GonkError(f"home.ssh_port is {home.ssh_port}, which is not a port number.")


def target(config: Config, address: str) -> str:
    return f"{config.home.user}@{address}" if config.home.user else address


def shell_command(config: Config, address: str, *, tmux: bool = True) -> list[str]:
    """ssh into home; attach to a persistent tmux session if home has tmux."""
    validate(config)
    argv = ["ssh", "-t"]
    if config.home.ssh_port != 22:
        argv += ["-p", str(config.home.ssh_port)]
    argv.append(target(config, address))
    if tmux:
        session = config.home.tmux_session  # validated above: safe to place in a command
        argv.append(
            f"command -v tmux >/dev/null 2>&1 && exec tmux new-session -A -s {session}; "
            'exec "${SHELL:-/bin/sh}" -l'
        )
    return argv


def code_command(config: Config, address: str, path: str = "") -> list[str]:
    """Open VS Code against home through Remote-SSH."""
    validate(config)
    argv = ["code", "--remote", f"ssh-remote+{target(config, address)}"]
    folder = path or config.home.code_path
    if folder:
        if folder.startswith("-"):
            raise GonkError(f"'{folder}' is not a folder path.")
        argv.append(folder)
    return argv
