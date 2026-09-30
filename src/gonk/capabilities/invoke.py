"""The single path by which a capability gets called from outside."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from gonk.capabilities.loader import build_registry
from gonk.capabilities.registry import Capability, CapabilityError, Context, Registry
from gonk.core.audit import AuditLog
from gonk.core.config import Config
from gonk.policy import Policy

AUDIT_FILE = "audit.log"


class NotAllowed(CapabilityError):
    """Policy said no, or there is no such capability. Callers are told the same
    thing in both cases, so that probing reveals nothing about what exists."""


@dataclass
class Gate:
    """Registry + policy + audit. The agent and the MCP server each hold one."""

    registry: Registry
    policy: Policy
    audit: AuditLog
    interface: str  # "agent" or "mcp"

    def allowed(self) -> list[Capability]:
        return [item for item in self.registry.all() if self.policy.check(item).allowed]

    def call(self, context: Context, name: str, arguments: dict[str, Any]) -> Any:
        item = self.registry.get(name)
        verdict = self.policy.check(item) if item else None
        if item is None or verdict is None or not verdict.allowed:
            self.audit.record(
                "capability.denied",
                interface=self.interface,
                caller=context.caller,
                capability=name,
                reason=verdict.reason if verdict else "no such capability",
            )
            raise NotAllowed(
                f"'{name}' is not available.",
                hints=["gonk mcp list    # on the machine that serves it, shows why"],
            )

        # Recorded before it runs: if the log cannot be written, nothing happens.
        self.audit.record(
            "capability.call",
            interface=self.interface,
            caller=context.caller,
            capability=name,
            risk=item.risk,
            arguments=arguments,
        )
        try:
            return item.call(context, arguments)
        except CapabilityError as error:
            self.audit.record_quietly(
                "capability.failed", caller=context.caller, capability=name, error=error.message
            )
            raise
        except Exception as error:
            # A bug in a capability or plugin. The caller is told that it
            # failed; what exactly went wrong stays on this machine.
            self.audit.record_quietly(
                "capability.crashed", caller=context.caller, capability=name, error=repr(error)
            )
            raise CapabilityError(
                f"{name} failed unexpectedly.",
                hints=["gonk log    # on the machine that serves it, has the details"],
            ) from error


def audit_log(config: Config) -> AuditLog:
    return AuditLog(config.state_directory / AUDIT_FILE)


def build_gate(config: Config, interface: str) -> Gate:
    return Gate(
        registry=build_registry(config.directory),
        policy=Policy.from_config(config),
        audit=audit_log(config),
        interface=interface,
    )
