"""Transports: how the network path to home is established and checked.

A transport answers three questions. Is the path up? What do I dial? What
should the user do if it is not working? Everything above this layer is
ordinary SSH and HTTP.
"""

from __future__ import annotations

import json
from typing import Any, Protocol

from gonk.core.config import Config
from gonk.core.errors import Check, GonkError
from gonk.core.system import System


class Transport(Protocol):
    name: str

    def checks(self, config: Config, system: System) -> list[Check]: ...

    def address(self, config: Config, system: System) -> str: ...


def _short(name: str) -> str:
    return name.rstrip(".").split(".")[0].lower()


class Tailscale:
    name = "tailscale"

    def _status(self, system: System) -> dict[str, Any] | None:
        result = system.run(["tailscale", "status", "--json"], timeout=10)
        if not result.ok:
            return None
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def _peer(self, status: dict[str, Any], host: str) -> dict[str, Any] | None:
        wanted = host.rstrip(".").lower()
        peers = list((status.get("Peer") or {}).values())
        if isinstance(status.get("Self"), dict):
            peers.append(status["Self"])
        for peer in peers:
            names = {
                str(peer.get("HostName", "")).lower(),
                str(peer.get("DNSName", "")).rstrip(".").lower(),
                _short(str(peer.get("DNSName", ""))),
            }
            if wanted in names or wanted in (peer.get("TailscaleIPs") or []):
                return dict(peer)
        return None

    def checks(self, config: Config, system: System) -> list[Check]:
        host = config.home.host
        if system.which("tailscale") is None:
            return [
                Check(
                    "Tailscale installed",
                    "fail",
                    "not found",
                    "gonk tools install tailscale",
                )
            ]
        checks = [Check("Tailscale installed", "ok")]

        status = self._status(system)
        state = status.get("BackendState", "unknown") if status else "not running"
        if status is None or state != "Running":
            hint = "sudo tailscale up"
            if state == "not running":
                hint = "sudo systemctl start tailscaled && sudo tailscale up"
            checks.append(Check("Tailscale connected", "fail", str(state), hint))
            return checks
        checks.append(Check("Tailscale connected", "ok"))

        peer = self._peer(status, host)
        if peer is None:
            checks.append(
                Check(
                    f"{host} is in your tailnet",
                    "fail",
                    "no machine by that name",
                    "tailscale status    # then: gonk config set home.host <name>",
                )
            )
        elif not peer.get("Online", False) and peer is not status.get("Self"):
            checks.append(
                Check(
                    f"{host} is online",
                    "fail",
                    "in your tailnet, but offline",
                    f"Check that {host} is switched on and running Tailscale.",
                )
            )
        else:
            addresses = peer.get("TailscaleIPs") or [""]
            checks.append(Check(f"{host} is online", "ok", str(addresses[0])))
        return checks

    def address(self, config: Config, system: System) -> str:
        host = config.home.host
        if system.resolves(host):
            return host  # MagicDNS, or an entry the user set up themselves
        status = self._status(system)
        peer = self._peer(status, host) if status else None
        addresses = (peer or {}).get("TailscaleIPs") or []
        return str(addresses[0]) if addresses else host


class Ssh:
    """Any host that SSH can already reach: WireGuard, a LAN, a jump host."""

    name = "ssh"

    def checks(self, config: Config, system: System) -> list[Check]:
        host = config.home.host
        if system.resolves(host):
            return [Check(f"{host} resolves", "ok")]
        return [
            Check(
                f"{host} resolves",
                "info",
                "not in DNS; assuming it is an alias in ~/.ssh/config",
            )
        ]

    def address(self, config: Config, system: System) -> str:
        return config.home.host


TRANSPORTS: dict[str, Transport] = {"tailscale": Tailscale(), "ssh": Ssh()}


def transport_for(config: Config) -> Transport:
    name = config.home.transport
    if name not in TRANSPORTS:
        raise GonkError(
            f"'{name}' is not a transport Gonk knows.",
            hints=[f"gonk config set home.transport {next(iter(TRANSPORTS))}"],
        )
    return TRANSPORTS[name]
