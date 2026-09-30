"""The allow-list. Every capability call is checked here first."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from fnmatch import fnmatchcase

from gonk.capabilities.registry import Capability
from gonk.core.config import Config


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    reason: str


class Policy:
    """Four rules, applied in order:

    1. A matching `deny` pattern always wins.
    2. Safe capabilities are allowed.
    3. Sensitive capabilities need a matching `allow` pattern; wildcards count.
    4. Dangerous capabilities need their exact name in `allow`; wildcards do not count.
    """

    def __init__(self, allow: Iterable[str] = (), deny: Iterable[str] = ()) -> None:
        self.allow = tuple(allow)
        self.deny = tuple(deny)

    @classmethod
    def from_config(cls, config: Config) -> Policy:
        return cls(config.policy.allow, config.policy.deny)

    def check(self, capability: Capability) -> Verdict:
        name = capability.name
        for pattern in self.deny:
            if fnmatchcase(name, pattern):
                return Verdict(False, f"denied by policy.deny: {pattern}")

        if capability.risk == "safe":
            return Verdict(True, "safe")

        if capability.risk == "dangerous":
            if name in self.allow:
                return Verdict(True, "named in policy.allow")
            return Verdict(
                False,
                f"dangerous: add exactly '{name}' to policy.allow (wildcards do not count)",
            )

        for pattern in self.allow:
            if fnmatchcase(name, pattern):
                return Verdict(True, f"allowed by policy.allow: {pattern}")
        return Verdict(False, f"sensitive: add '{name}' to policy.allow")
