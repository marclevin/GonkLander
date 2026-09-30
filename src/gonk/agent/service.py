"""Run the agent in the foreground, or manage it as a systemd user service."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

from gonk.agent.app import create_app
from gonk.agent.bind import resolve_bind
from gonk.agent.tokens import DeviceStore
from gonk.capabilities.invoke import build_gate
from gonk.core.config import Config
from gonk.core.errors import GonkError
from gonk.core.system import Result, System

UNIT_NAME = "gonk-agent.service"

UNIT_TEMPLATE = """\
[Unit]
Description=Gonk Agent
Documentation=https://github.com/marclevin/GonkLander
After=network-online.target

[Service]
Type=simple
ExecStart={command}
Restart=on-failure
RestartSec=5
# The agent never needs more privilege than it starts with.
NoNewPrivileges=yes

[Install]
WantedBy=default.target
"""


def run(config: Config, system: System) -> None:
    """Serve until interrupted."""
    import uvicorn

    if not config.agent.enabled:
        raise GonkError(
            "The agent is switched off in config.",
            hints=["gonk config set agent.enabled true"],
        )
    devices = DeviceStore(config.directory)
    if not devices.devices():
        raise GonkError(
            "No devices are allowed to connect yet, so there is nothing to serve.",
            hints=["gonk agent token create <device-name>"],
        )
    host = resolve_bind(config, system)
    gate = build_gate(config, "agent")
    gate.audit.record("agent.start", bind=host, port=config.agent.port)
    app = create_app(config, system, gate, devices)
    uvicorn.run(app, host=host, port=config.agent.port, log_level="warning", access_log=False)


# --- systemd ---------------------------------------------------------------


def unit_path(system: System) -> Path:
    base = system.env.get("XDG_CONFIG_HOME") or str(system.home() / ".config")
    return Path(base) / "systemd" / "user" / UNIT_NAME


def gonk_executable(system: System) -> str:
    found = system.which("gonk")
    if found:
        return found
    candidate = Path(sys.argv[0]).resolve()
    if candidate.name == "gonk" and candidate.exists():
        return str(candidate)
    raise GonkError(
        "Could not find the gonk executable to put in the service file.",
        hints=["Make sure ~/.local/bin is on your PATH, then try again."],
    )


def unit_text(executable: str) -> str:
    return UNIT_TEMPLATE.format(command=f"{shlex.quote(executable)} agent run")


def systemctl(system: System, *arguments: str) -> Result:
    if system.which("systemctl") is None:
        raise GonkError(
            "This machine does not use systemd, so Gonk cannot manage the agent as a service.",
            hints=["gonk agent run    # runs it in the foreground instead"],
        )
    return system.run(["systemctl", "--user", *arguments], timeout=30)


def require(result: Result, doing: str) -> None:
    if not result.ok:
        raise GonkError(
            f"Could not {doing}: {result.text or 'systemctl failed'}",
            hints=[f"journalctl --user -u {UNIT_NAME} -n 30"],
        )


def install(system: System) -> Path:
    path = unit_path(system)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(unit_text(gonk_executable(system)), encoding="utf-8")
    require(systemctl(system, "daemon-reload"), "reload systemd")
    require(systemctl(system, "enable", UNIT_NAME), "enable the agent")
    return path


def uninstall(system: System) -> None:
    systemctl(system, "disable", "--now", UNIT_NAME)
    unit_path(system).unlink(missing_ok=True)
    systemctl(system, "daemon-reload")


def state(system: System) -> str:
    """not-installed | active | inactive | failed | unknown"""
    if not unit_path(system).exists():
        return "not-installed"
    if system.which("systemctl") is None:
        return "unknown"
    result = system.run(["systemctl", "--user", "is-active", UNIT_NAME], timeout=10)
    return result.stdout.strip() or "unknown"
