"""Capabilities: the named, typed, risk-rated things Gonk can do on a machine.

Writing one takes a decorator and a function:

    from gonk.capabilities import Context, capability

    @capability("machine.status", risk="safe", description="Uptime and load.")
    def machine_status(ctx: Context) -> dict:
        ...
"""

from gonk.capabilities.registry import (
    Capability,
    CapabilityError,
    Context,
    Registry,
    capability,
)

__all__ = ["Capability", "CapabilityError", "Context", "Registry", "capability"]
