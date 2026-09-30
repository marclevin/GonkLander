"""About Gonk itself."""

from __future__ import annotations

from typing import Any

from gonk import __version__
from gonk.capabilities.registry import Context, capability
from gonk.core import platform


@capability("gonk.status", risk="safe", description="Gonk version, hostname and platform.")
def status(ctx: Context) -> dict[str, Any]:
    machine = platform.detect(ctx.system)
    hostname = ctx.system.hostname()
    return {
        "gonk_version": __version__,
        "hostname": hostname,
        "is_home": hostname == ctx.config.home.name,
        "os": machine.pretty_name,
        "arch": machine.arch,
    }
