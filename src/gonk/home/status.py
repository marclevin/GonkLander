"""Check the path to home, one layer at a time."""

from __future__ import annotations

from dataclasses import dataclass, field

from gonk.core.config import Config, home_token
from gonk.core.errors import Check, GonkError
from gonk.core.system import System
from gonk.home.client import AgentClient, AgentRefused, AgentUnreachable
from gonk.home.transport import transport_for


@dataclass
class HomeReport:
    checks: list[Check] = field(default_factory=list)
    address: str = ""
    is_here: bool = False

    @property
    def network_ok(self) -> bool:
        return all(check.ok for check in self.checks if check.name != AGENT_CHECK)

    @property
    def reachable(self) -> bool:
        return all(check.ok for check in self.checks)


AGENT_CHECK = "Gonk Agent"


def client_for(config: Config, system: System, address: str) -> AgentClient:
    return AgentClient(
        address,
        config.home.agent_port,
        home_token(config, system.env),
        name=config.home.name,
        timeout=5,
    )


def agent_check(config: Config, system: System, address: str) -> Check:
    name = config.home.name
    if not home_token(config, system.env):
        return Check(
            AGENT_CHECK,
            "warn",
            "this device has no token",
            f"On {name}: gonk agent token create <this-device>   then here: gonk home pair",
        )
    client = client_for(config, system, address)
    try:
        identity = client.whoami()
    except AgentRefused:
        return Check(
            AGENT_CHECK,
            "fail",
            "running, but it rejected this device's token",
            f"On {name}: gonk agent token create <this-device>   then here: gonk home pair",
        )
    except AgentUnreachable:
        return Check(
            AGENT_CHECK,
            "warn",
            f"not answering on port {config.home.agent_port}",
            f"On {name}: gonk agent start",
        )
    finally:
        client.close()
    return Check(
        AGENT_CHECK,
        "ok",
        f"v{identity.get('version', '?')}, signed in as {identity.get('device', '?')}",
    )


def inspect(config: Config, system: System) -> HomeReport:
    """Transport first, then SSH, then the agent. Stops at the first broken layer,
    because everything after it would fail for the same reason."""
    report = HomeReport()
    name = config.home.name

    if system.hostname().lower() == name.lower():
        report.is_here = True
        report.address = "127.0.0.1"
        report.checks.append(Check(f"This machine is {name}", "ok"))
        return report

    try:
        transport = transport_for(config)
    except GonkError as error:
        report.checks.append(Check("Transport", "fail", error.message, "; ".join(error.hints)))
        return report

    report.checks.extend(transport.checks(config, system))
    if not report.network_ok:
        return report

    report.address = transport.address(config, system)
    port = config.home.ssh_port
    problem = system.tcp_probe(report.address, port)
    if problem:
        report.checks.append(
            Check(
                f"SSH on {name}",
                "fail",
                f"port {port}: {problem}",
                f"Check that sshd is running on {name} and listening on port {port}.",
            )
        )
        return report
    report.checks.append(Check(f"SSH on {name}", "ok", f"{report.address}:{port}"))
    report.checks.append(agent_check(config, system, report.address))
    return report
