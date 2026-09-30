"""Look at, and restart, the services the owner has named in config."""

from __future__ import annotations

import re
from typing import Any

from gonk.capabilities.registry import CapabilityError, Context, capability

UNIT_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_.@-]*$")
USER_PREFIX = "user:"
SHOWN = "Description,LoadState,ActiveState,SubState,ActiveEnterTimestamp,MainPID,NRestarts"


def _systemctl(ctx: Context, name: str) -> list[str]:
    """The start of a systemctl command for a service that config allows.

    Config lists services as "nginx.service" or, for the user's own units,
    "user:gonk-agent.service". The caller must use the same spelling.
    """
    allowed = ctx.config.services.allowed
    if name not in allowed:
        listed = ", ".join(allowed) if allowed else "none"
        raise CapabilityError(
            f"'{name}' is not in services.allowed. Allowed services: {listed}.",
            hints=["Add it to services.allowed in config.yaml on the machine that serves it."],
        )
    unit = name.removeprefix(USER_PREFIX)
    if not UNIT_NAME.match(unit):
        raise CapabilityError(f"'{unit}' is not a valid systemd unit name.")
    scope = ["--user"] if name.startswith(USER_PREFIX) else []
    return ["systemctl", *scope]


def _show(ctx: Context, name: str) -> dict[str, Any]:
    unit = name.removeprefix(USER_PREFIX)
    result = ctx.system.run([*_systemctl(ctx, name), "show", unit, f"--property={SHOWN}"])
    if not result.ok:
        raise CapabilityError(f"Could not read the status of {name}: {result.text}")
    fields = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    return {
        "name": name,
        "description": fields.get("Description", ""),
        "loaded": fields.get("LoadState", ""),
        "active": fields.get("ActiveState", ""),
        "state": fields.get("SubState", ""),
        "since": fields.get("ActiveEnterTimestamp", ""),
        "pid": fields.get("MainPID", ""),
        "restarts": fields.get("NRestarts", ""),
    }


@capability(
    "services.status",
    risk="sensitive",
    description="Status of one service. Only services listed in services.allowed.",
)
def status(ctx: Context, name: str) -> dict[str, Any]:
    return _show(ctx, name)


@capability(
    "services.restart",
    risk="dangerous",
    description="Restart one service. Only services listed in services.allowed.",
)
def restart(ctx: Context, name: str) -> dict[str, Any]:
    unit = name.removeprefix(USER_PREFIX)
    # Gonk does not use sudo here. Restarting a system service works only if
    # the machine's own rules (polkit) let this user do it.
    result = ctx.system.run([*_systemctl(ctx, name), "restart", unit], timeout=60)
    if not result.ok:
        raise CapabilityError(
            f"Could not restart {name}: {result.text or 'systemctl failed'}",
            hints=["System services need a polkit rule; user services use the 'user:' prefix."],
        )
    return {"restarted": True, **_show(ctx, name)}
