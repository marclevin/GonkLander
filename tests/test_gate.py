from __future__ import annotations

from pathlib import Path

import pytest

from gonk.capabilities.invoke import Gate, NotAllowed
from gonk.capabilities.loader import build_registry
from gonk.capabilities.registry import CapabilityError, Context, capability
from gonk.core.audit import AuditLog
from gonk.core.config import Config
from gonk.policy import Policy

from .fakes import FakeSystem


def gate(tmp_path: Path, *allow: str) -> Gate:
    return Gate(build_registry(None), Policy(allow=allow), AuditLog(tmp_path / "audit.log"), "mcp")


def test_only_allowed_capabilities_are_listed(tmp_path: Path) -> None:
    names = {item.name for item in gate(tmp_path).allowed()}
    assert "machine.status" in names
    assert "files.read" not in names
    assert "commands.run" not in names
    assert "files.read" in {item.name for item in gate(tmp_path, "files.read").allowed()}


def test_forbidden_and_nonexistent_look_the_same(
    tmp_path: Path, config: Config, system: FakeSystem
) -> None:
    context = Context(config, system, caller="laptop")
    with pytest.raises(NotAllowed) as forbidden:
        gate(tmp_path).call(context, "commands.run", {"name": "x"})
    with pytest.raises(NotAllowed) as missing:
        gate(tmp_path).call(context, "commands.ruin", {"name": "x"})
    assert forbidden.value.message.replace("commands.run", "X") == missing.value.message.replace(
        "commands.ruin", "X"
    )


def test_calls_and_refusals_are_recorded(
    tmp_path: Path, config: Config, system: FakeSystem
) -> None:
    made = gate(tmp_path)
    context = Context(config, system, caller="laptop")
    made.call(context, "gonk.status", {})
    with pytest.raises(NotAllowed):
        made.call(context, "files.read", {"path": "/etc/shadow"})

    called, denied = made.audit.tail()
    assert (called["event"], called["capability"], called["caller"]) == (
        "capability.call",
        "gonk.status",
        "laptop",
    )
    assert (denied["event"], denied["capability"]) == ("capability.denied", "files.read")
    assert "policy.allow" in denied["reason"]


def test_a_crash_is_recorded_here_and_kept_from_the_caller(
    tmp_path: Path, config: Config, system: FakeSystem
) -> None:
    @capability("demo.crash", risk="safe", description="Always breaks.")
    def crash(ctx: Context) -> None:
        raise RuntimeError("password=hunter2 in /home/marc/.secret")

    made = gate(tmp_path)
    made.registry.add_from({"crash": crash}, "plugin demo.py")
    with pytest.raises(CapabilityError) as caught:
        made.call(Context(config, system), "demo.crash", {})
    assert str(caught.value).startswith("demo.crash failed unexpectedly.")
    assert "hunter2" not in str(caught.value)
    assert "hunter2" in made.audit.tail()[-1]["error"]


def test_if_it_cannot_be_recorded_it_does_not_happen(tmp_path: Path, config: Config) -> None:
    config.commands = {"disk": ["df"]}
    system = FakeSystem(installed={"df": ""})
    blocked = tmp_path / "a-file-not-a-directory"
    blocked.write_text("")
    made = Gate(
        build_registry(None),
        Policy(allow=["commands.run"]),
        AuditLog(blocked / "audit.log"),
        "agent",
    )
    with pytest.raises(OSError):
        made.call(Context(config, system), "commands.run", {"name": "disk"})
    assert system.ran == []


def test_what_callers_send_cannot_garble_the_terminal(
    tmp_path: Path, config: Config, system: FakeSystem
) -> None:
    made = gate(tmp_path)
    hostile = "x.y\x1b[2J\x1b]0;owned\x07\nfake.entry"
    with pytest.raises(NotAllowed):
        made.call(Context(config, system), hostile, {"k\x1b": ["\x1b[31m"]})
    raw = made.audit.path.read_text()
    assert "\x1b" not in raw
    assert "\\u001b" not in raw
    assert len(raw.splitlines()) == 1  # and it cannot forge a second entry


def test_the_audit_log_is_private_and_clipped(tmp_path: Path) -> None:
    log = AuditLog(tmp_path / "state" / "audit.log")
    log.record("test", value="x" * 5000)
    assert log.path.stat().st_mode & 0o077 == 0
    assert len(log.tail()[0]["value"]) < 300
