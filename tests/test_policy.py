"""MCP and agent permission handling."""

from __future__ import annotations

from typing import Any

import pytest

from gonk.capabilities.registry import Capability, Risk
from gonk.core.config import Config
from gonk.policy import Policy


def cap(name: str, risk: Risk) -> Capability:
    def handler(ctx: Any) -> None:
        return None

    return Capability(name, "test", risk, handler)


SAFE = cap("machine.status", "safe")
SENSITIVE = cap("files.read", "sensitive")
DANGEROUS = cap("services.restart", "dangerous")


def test_with_no_configuration_only_safe_capabilities_are_allowed() -> None:
    policy = Policy()
    assert policy.check(SAFE).allowed
    assert not policy.check(SENSITIVE).allowed
    assert not policy.check(DANGEROUS).allowed


def test_the_default_config_allows_nothing_beyond_safe() -> None:
    policy = Policy.from_config(Config())
    assert not policy.check(SENSITIVE).allowed
    assert not policy.check(DANGEROUS).allowed


def test_sensitive_can_be_allowed_by_name_or_wildcard() -> None:
    assert Policy(allow=["files.read"]).check(SENSITIVE).allowed
    assert Policy(allow=["files.*"]).check(SENSITIVE).allowed
    assert not Policy(allow=["mphil.*"]).check(SENSITIVE).allowed


def test_dangerous_must_be_named_exactly() -> None:
    assert Policy(allow=["services.restart"]).check(DANGEROUS).allowed


@pytest.mark.parametrize("pattern", ["*", "services.*", "*.restart", "services.restar?", "s*"])
def test_a_wildcard_never_grants_a_dangerous_capability(pattern: str) -> None:
    verdict = Policy(allow=[pattern]).check(DANGEROUS)
    assert not verdict.allowed
    assert "wildcards do not count" in verdict.reason


def test_allowing_everything_still_leaves_dangerous_off() -> None:
    policy = Policy(allow=["*"])
    assert policy.check(SENSITIVE).allowed
    assert not policy.check(DANGEROUS).allowed
    assert not policy.check(cap("commands.run", "dangerous")).allowed


def test_deny_beats_everything() -> None:
    assert not Policy(deny=["machine.*"]).check(SAFE).allowed
    assert not Policy(allow=["files.read"], deny=["files.read"]).check(SENSITIVE).allowed
    policy = Policy(allow=["services.restart"], deny=["services.*"])
    assert not policy.check(DANGEROUS).allowed


def test_deny_everything() -> None:
    policy = Policy(allow=["*", "services.restart"], deny=["*"])
    assert not any(policy.check(item).allowed for item in (SAFE, SENSITIVE, DANGEROUS))


def test_matching_is_case_sensitive() -> None:
    assert not Policy(allow=["FILES.READ"]).check(SENSITIVE).allowed


def test_a_refusal_says_what_to_change() -> None:
    assert "add 'files.read' to policy.allow" in Policy().check(SENSITIVE).reason
    assert "add exactly 'services.restart'" in Policy().check(DANGEROUS).reason
