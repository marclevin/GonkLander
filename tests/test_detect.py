from __future__ import annotations

from pathlib import Path

import pytest

from gonk.core.errors import GonkError
from gonk.core.system import Result
from gonk.tools.catalog import ToolSpec, load_catalog, parse_catalog
from gonk.tools.detect import detect, parse_version, version_at_least
from gonk.tools.providers import PROVIDERS

from .fakes import FakeSystem


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("git version 2.43.0", (2, 43, 0)),
        ("jq-1.7.1", (1, 7, 1)),
        ("v22.11.0", (22, 11, 0)),
        ("Python 3.12.3", (3, 12, 3)),
        ("OpenSSH_9.6p1 Ubuntu-3ubuntu13.5, OpenSSL 3.0.13", (9, 6)),
        ("tmux 3.4", (3, 4)),
        ("Docker version 27.3.1, build ce12230", (27, 3, 1)),
        ("uv 0.5.4 (c62c83c37 2024-11-20)", (0, 5, 4)),
        ("version 7", (7,)),
        ("no digits here", None),
        ("", None),
    ],
)
def test_parse_version(text: str, expected: tuple[int, ...] | None) -> None:
    assert parse_version(text) == expected


@pytest.mark.parametrize(
    ("found", "wanted", "expected"),
    [
        ("1.7.1", "1.6", True),
        ("1.6", "1.6", True),
        ("1.6.0", "1.6", True),
        ("1.5.9", "1.6", False),
        ("22.11.0", "18", True),
        ("2.9", "2.10", False),  # compared as numbers, not text
        ("16.20.2", "18", False),
        ("anything", "", True),  # no requirement
        ("", "1.6", True),  # cannot measure: leave it alone
    ],
)
def test_version_at_least(found: str, wanted: str, expected: bool) -> None:
    assert version_at_least(found, wanted) is expected


def test_detects_a_present_tool() -> None:
    spec = ToolSpec("jq", commands=("jq",), min_version="1.6")
    found = detect(spec, FakeSystem(installed={"jq": "jq-1.7.1"}))
    assert (found.present, found.version, found.acceptable) == (True, "1.7.1", True)
    assert found.path == "/usr/bin/jq"


def test_detects_a_missing_tool_without_running_anything() -> None:
    system = FakeSystem()
    found = detect(ToolSpec("jq", commands=("jq",)), system)
    assert not found.present
    assert system.ran == []


def test_detects_an_old_tool() -> None:
    spec = ToolSpec("node", commands=("node",), min_version="18")
    found = detect(spec, FakeSystem(installed={"node": "v16.20.2"}))
    assert found.present
    assert not found.acceptable


def test_any_of_several_command_names_counts() -> None:
    spec = ToolSpec("fd", commands=("fd", "fdfind"))
    found = detect(spec, FakeSystem(installed={"fdfind": "fdfind 9.0.0"}))
    assert found.present
    assert found.path == "/usr/bin/fdfind"


def test_version_printed_on_stderr() -> None:
    system = FakeSystem(
        installed={"ssh": ""}, results={"ssh -V": Result(0, "", "OpenSSH_9.6p1 Ubuntu")}
    )
    spec = ToolSpec("ssh", commands=("ssh",), version_args=("-V",))
    assert detect(spec, system).version == "9.6"


def test_a_tool_that_will_not_say_its_version_is_still_present() -> None:
    system = FakeSystem(installed={"odd": ""}, results={"odd --version": Result(2, "", "")})
    found = detect(ToolSpec("odd", commands=("odd",), min_version="1.0"), system)
    assert found.present
    assert found.acceptable


# --- catalog -----------------------------------------------------------------


def test_builtin_catalog_has_the_tools_marc_cares_about() -> None:
    wanted = {
        "git", "gh", "python", "uv", "node", "npm", "pnpm", "docker",
        "ripgrep", "jq", "tmux", "claude-code", "codex", "tailscale",
    }  # fmt: skip
    assert wanted <= set(load_catalog())


def test_every_catalog_entry_produces_valid_commands() -> None:
    """Catches a typo in tools.yaml before it reaches a real machine."""
    system = FakeSystem()
    for spec in load_catalog().values():
        assert spec.commands, spec.name
        assert spec.install, f"{spec.name} has no way to be installed"
        for provider, options in spec.install.items():
            assert provider in PROVIDERS, f"{spec.name}: unknown provider {provider}"
            commands = PROVIDERS[provider].commands(options, system)
            assert commands and all(command.argv for command in commands)


def test_every_requirement_is_in_the_catalog() -> None:
    catalog = load_catalog()
    for spec in catalog.values():
        assert set(spec.requires) <= set(catalog), spec.name


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("jq: nonsense", "should be a set of settings"),
        ("jq:\n  instal: {}", "unknown setting"),
        ("jq:\n  check: {command: jq}", "check has unknown setting"),
        ("jq:\n  check: {commands: jq}", "list of words"),
        ("jq:\n  install: [apt]", "map provider names"),
        ("- a\n- b", "should map tool names"),
    ],
)
def test_bad_catalogs_are_explained(text: str, message: str) -> None:
    with pytest.raises(GonkError, match=message):
        parse_catalog(text, "tools.yaml")


def test_user_catalog_adds_and_overrides(tmp_path: Path) -> None:
    (tmp_path / "tools.yaml").write_text(
        "mytool:\n  install:\n    apt: {packages: [mytool]}\n"
        "jq:\n  check: {commands: [jq], min_version: '9'}\n"
    )
    catalog = load_catalog(tmp_path)
    assert catalog["mytool"].commands == ("mytool",)  # defaults to the tool's own name
    assert catalog["jq"].min_version == "9"
    assert "git" in catalog
