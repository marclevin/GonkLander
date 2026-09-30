"""Decide which address the agent listens on, and refuse the unsafe ones."""

from __future__ import annotations

import ipaddress

from gonk.core.config import Config
from gonk.core.errors import GonkError
from gonk.core.system import System

# Tailscale hands out addresses from these ranges; nothing outside the tailnet
# can route to them.
TAILNET = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))


def is_private_to_you(address: str) -> bool:
    """True for loopback and tailnet addresses: reachable only by your own devices."""
    try:
        parsed = ipaddress.ip_address(address)
    except ValueError:
        return False
    return parsed.is_loopback or any(parsed in network for network in TAILNET)


def resolve_bind(config: Config, system: System) -> str:
    bind = config.agent.bind.strip()
    if bind == "localhost":
        bind = "127.0.0.1"

    if bind == "tailscale":
        result = system.run(["tailscale", "ip", "-4"], timeout=10)
        address = result.stdout.strip().splitlines()[0] if result.ok and result.stdout else ""
        if not address:
            raise GonkError(
                "agent.bind is 'tailscale', but this machine has no tailnet address.",
                hints=["sudo tailscale up", "tailscale ip -4"],
            )
        bind = address

    try:
        ipaddress.ip_address(bind)
    except ValueError:
        raise GonkError(
            f"agent.bind is '{bind}', which is not an address.",
            hints=["gonk config set agent.bind 127.0.0.1", "gonk config set agent.bind tailscale"],
        ) from None

    if not is_private_to_you(bind) and not config.agent.allow_public_bind:
        raise GonkError(
            f"Refusing to listen on {bind}: it may be reachable by machines that are not yours.",
            hints=[
                "gonk config set agent.bind tailscale    # only your tailnet",
                "gonk config set agent.bind 127.0.0.1    # only this machine",
                "Read docs/security.md before setting agent.allow_public_bind.",
            ],
        )
    return bind
