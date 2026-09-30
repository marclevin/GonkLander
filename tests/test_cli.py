"""Command-line parsing and the behaviour of commands, against a fake machine."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from gonk import __version__
from gonk.cli import common, main
from gonk.cli.main import app

from .fakes import UBUNTU, FakeSystem, Replaced

runner = CliRunner()


@pytest.fixture
def machine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeSystem:
    """Every command in this file runs on this machine instead of the real one."""
    system = FakeSystem(
        installed={
            "apt-get": "",
            "sudo": "",
            "git": "git version 2.43.0",
            "curl": "curl 8.5.0",
            "jq": "jq-1.7.1",
            "ssh": "OpenSSH_9.6",
            "tmux": "tmux 3.4",
        },
        packages={"ripgrep": ["rg"]},
        files={"/etc/os-release": UBUNTU},
        env={
            "GONK_CONFIG_DIR": str(tmp_path / "config"),
            "GONK_STATE_DIR": str(tmp_path / "state"),
        },
        home=tmp_path / "home",
    )
    monkeypatch.setattr(common, "System", lambda: system)
    monkeypatch.setattr(main, "System", lambda: system)
    return system


def run(*arguments: str) -> tuple[int, str]:
    """Run through main(), so that errors are rendered the way users see them."""
    from gonk.core import ui

    with runner.isolation() as streams:
        ui.out.file = ui.err.file = None  # type: ignore[assignment]  # follow sys.stdout/stderr
        code = 0
        try:
            sys.argv = ["gonk", *arguments]
            main.main()
        except SystemExit as stop:
            code = int(stop.code or 0)
        sys.stdout.flush()
        sys.stderr.flush()
        output = streams[0].getvalue().decode()
        if streams[1] is not None:
            output += streams[1].getvalue().decode()
    return code, output


# --- parsing ---------------------------------------------------------------------


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == f"gonk {__version__}"


COMMANDS = [
    ["status"], ["doctor"], ["land"], ["update"], ["profiles"], ["log"],
    ["tools"], ["tools", "list"], ["tools", "install"],
    ["config"], ["config", "path"], ["config", "show"], ["config", "init"], ["config", "set"],
    ["home"], ["home", "status"], ["home", "shell"], ["home", "code"], ["home", "pair"],
    ["agent"], ["agent", "run"], ["agent", "install"], ["agent", "uninstall"],
    ["agent", "start"], ["agent", "stop"], ["agent", "status"],
    ["agent", "token"], ["agent", "token", "create"], ["agent", "token", "list"],
    ["agent", "token", "revoke"],
    ["mcp"], ["mcp", "list"], ["mcp", "status"], ["mcp", "install"], ["mcp", "serve"],
]  # fmt: skip


@pytest.mark.parametrize("command", COMMANDS, ids=" ".join)
def test_every_command_exists_and_explains_itself(command: list[str]) -> None:
    result = runner.invoke(app, [*command, "--help"])
    assert result.exit_code == 0, result.output
    assert "Usage:" in result.output


def test_unknown_commands_are_refused() -> None:
    assert runner.invoke(app, ["frobnicate"]).exit_code == 2
    assert runner.invoke(app, ["home", "teleport"]).exit_code == 2
    assert runner.invoke(app, ["land", "dev", "--force-everything"]).exit_code == 2


def test_required_arguments() -> None:
    assert runner.invoke(app, ["tools", "install"]).exit_code == 2
    assert runner.invoke(app, ["config", "set", "home.host"]).exit_code == 2
    assert runner.invoke(app, ["agent", "token", "create"]).exit_code == 2


# --- land ------------------------------------------------------------------------


def test_dry_run_changes_nothing(machine: FakeSystem) -> None:
    code, output = run("land", "minimal", "--dry-run")
    assert code == 0
    assert "ripgrep" in output
    assert "apt-get install" in output
    assert "Dry run: nothing was changed." in output
    assert machine.changed == []


def test_land_installs_only_what_is_missing(machine: FakeSystem) -> None:
    code, output = run("land", "minimal", "--yes")
    assert code == 0, output
    installs = [argv for argv in machine.changed if "install" in argv]
    assert len(installs) == 1
    assert installs[0][-1] == "ripgrep"
    assert installs[0][0] == "sudo"


def test_land_again_does_nothing(machine: FakeSystem) -> None:
    run("land", "minimal", "--yes")
    before = list(machine.changed)
    code, output = run("land", "minimal", "--yes")
    assert code == 0
    assert "Nothing to do." in output
    assert machine.changed == before


def test_land_uses_the_default_profile(machine: FakeSystem) -> None:
    _code, output = run("land", "--dry-run")
    assert "Landing profile 'dev'" in output


def test_land_will_not_install_without_being_able_to_ask(machine: FakeSystem) -> None:
    code, output = run("land", "minimal")  # no terminal in tests, and no --yes
    assert code == 1
    assert "Add --yes" in output
    assert machine.changed == []


def test_land_reports_failure(machine: FakeSystem) -> None:
    machine.exit_code = 100
    code, output = run("land", "minimal", "--yes")
    assert code == 1
    assert "did not install" in output
    assert "Running the same command again is safe" in output


def test_unknown_profile_is_explained(machine: FakeSystem) -> None:
    code, output = run("land", "nope")
    assert code == 1
    assert "There is no profile called 'nope'." in output
    assert "Available profiles:" in output
    assert "Traceback" not in output


# --- tools -----------------------------------------------------------------------


def test_tools_list(machine: FakeSystem) -> None:
    code, output = run("tools", "list")
    assert code == 0
    assert "jq" in output
    assert "1.7.1" in output
    assert "not installed" in output


def test_tools_list_as_json(machine: FakeSystem) -> None:
    code, output = run("tools", "list", "--json")
    tools = {tool["name"]: tool for tool in json.loads(output)}
    assert code == 0
    assert tools["jq"] == {
        "name": "jq",
        "installed": True,
        "version": "1.7.1",
        "path": "/usr/bin/jq",
    }
    assert tools["node"]["installed"] is False


def test_installing_an_unknown_tool_suggests_a_known_one(machine: FakeSystem) -> None:
    code, output = run("tools", "install", "rip")
    assert code == 1
    assert "Did you mean: ripgrep" in output
    assert machine.changed == []


def test_installing_a_present_tool_does_nothing(machine: FakeSystem) -> None:
    code, output = run("tools", "install", "jq", "--yes")
    assert code == 0
    assert "Nothing to do." in output
    assert machine.changed == []


# --- config ----------------------------------------------------------------------


def test_config_set_and_show(machine: FakeSystem, tmp_path: Path) -> None:
    assert run("config", "set", "home.host", "100.64.0.7")[0] == 0
    assert run("config", "set", "home.ssh_port", "2222")[0] == 0
    _code, output = run("config", "show")
    assert "host: 100.64.0.7" in output
    assert "ssh_port: 2222" in output
    assert run("config", "path")[1].strip() == str(tmp_path / "config" / "config.yaml")


def test_config_set_rejects_nonsense(machine: FakeSystem) -> None:
    code, output = run("config", "set", "home.ssh_port", "twenty-two")
    assert code == 1
    assert "should be a whole number" in output
    code, output = run("config", "set", "home.hots", "x")
    assert code == 1
    assert "is not a setting" in output


def test_config_init_never_overwrites(machine: FakeSystem, tmp_path: Path) -> None:
    assert "Wrote" in run("config", "init")[1]
    path = tmp_path / "config" / "config.yaml"
    path.write_text("home:\n  host: mine\n")
    assert "leaving it alone" in run("config", "init")[1]
    assert path.read_text() == "home:\n  host: mine\n"


def test_a_broken_config_stops_commands_but_not_the_doctor(
    machine: FakeSystem, tmp_path: Path
) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "config.yaml").write_text("home:\n  ssh_port: nope\n")
    code, output = run("land", "--dry-run")
    assert code == 1
    assert str(tmp_path / "config" / "config.yaml") in output  # whole, on one line
    assert "whole number" in output

    code, output = run("doctor")
    assert code == 1
    assert "Configuration" in output
    assert "System" in output


# --- doctor and status -----------------------------------------------------------


def test_doctor(machine: FakeSystem) -> None:
    machine.open_ports = {("gonksystem", 22)}
    code, output = run("config", "set", "home.transport", "ssh")
    code, output = run("doctor")
    assert code == 0, output
    assert "Ubuntu 24.04.1 LTS" in output
    assert "gonk tools install node" in output
    assert "No problems." in output


def test_doctor_as_json(machine: FakeSystem) -> None:
    _code, output = run("doctor", "--json")
    data = json.loads(output)
    assert [section["title"] for section in data["sections"]][:2] == ["System", "Tools"]


def test_bare_gonk_shows_status_and_where_to_go(machine: FakeSystem) -> None:
    code, output = run()
    assert code == 0
    assert f"gonk {__version__}" in output
    assert "gonk doctor" in output


# --- home ------------------------------------------------------------------------


def test_home_status_shows_the_broken_layer(machine: FakeSystem) -> None:
    code, output = run("home", "status")
    assert code == 1
    assert "Could not reach gonksystem." in output
    assert "✗ Tailscale installed" in output
    assert "gonk tools install tailscale" in output
    assert "Errno" not in output


def test_home_shell_becomes_ssh(machine: FakeSystem) -> None:
    run("config", "set", "home.transport", "ssh")
    run("config", "set", "home.user", "marc")
    machine.open_ports = {("gonksystem", 22)}
    with pytest.raises(Replaced) as replaced:
        run("home", "shell")
    assert replaced.value.argv == ["ssh", "-t", "marc@gonksystem"]
    with pytest.raises(Replaced) as replaced:
        run("home", "shell", "--tmux")
    assert "tmux" in replaced.value.argv[-1]


def test_home_shell_does_not_try_when_home_is_unreachable(machine: FakeSystem) -> None:
    code, output = run("home", "shell")  # would raise Replaced if ssh were started
    assert code == 1
    assert "Could not reach gonksystem." in output


def test_home_shell_on_home_itself(machine: FakeSystem) -> None:
    machine._hostname = "gonksystem"
    code, output = run("home", "shell")
    assert code == 1
    assert "You are already on gonksystem." in output


def test_home_code_without_vs_code(machine: FakeSystem) -> None:
    code, output = run("home", "code")
    assert code == 1
    assert "VS Code is not installed here" in output
    assert "gonk home shell" in output


# --- agent and mcp ---------------------------------------------------------------


def test_token_lifecycle(machine: FakeSystem) -> None:
    code, output = run("agent", "token", "create", "laptop")
    assert code == 0
    assert "gonk_" in output
    assert "laptop" in run("agent", "token", "list")[1]
    assert run("agent", "token", "revoke", "laptop")[0] == 0
    assert "No devices yet." in run("agent", "token", "list")[1]

    events = [line for line in run("log")[1].splitlines() if "agent.token" in line]
    assert len(events) == 2
    assert "gonk_" not in run("log")[1]  # the token itself is never logged


def test_agent_will_not_start_with_nobody_to_serve(machine: FakeSystem) -> None:
    code, output = run("agent", "run")
    assert code == 1
    assert "No devices are allowed to connect yet" in output


def test_agent_will_not_listen_publicly(machine: FakeSystem) -> None:
    run("agent", "token", "create", "laptop")
    run("config", "set", "agent.bind", "0.0.0.0")  # noqa: S104
    code, output = run("agent", "run")
    assert code == 1
    assert "Refusing to listen on 0.0.0.0" in output


def test_mcp_list_explains_policy(machine: FakeSystem) -> None:
    code, output = run("mcp", "list")
    assert code == 0
    assert "gonk_machine_status" in output
    assert "add 'files.read' to policy.allow" in output
    assert "wildcards do not count" in output

    assert "gonk_files_read" not in output  # a tool that is off has no MCP name to call

    run("config", "set", "policy.allow", "files.read")
    assert "gonk_files_read" in run("mcp", "list")[1]


def test_long_paths_in_errors_are_never_folded(machine: FakeSystem, tmp_path: Path) -> None:
    machine._home = tmp_path / ("very-" * 30 + "long")
    machine.env["GONK_CONFIG_DIR"] = str(machine._home / ".config" / "gonk")
    _code, output = run("config", "set", "home.ssh_port", "nope")
    assert "\n" not in output.strip()


def test_tables_fit_a_narrow_terminal(machine: FakeSystem) -> None:
    for command in (["mcp", "list"], ["tools", "list"], ["profiles"]):
        _code, output = run(*command)
        assert max(len(line) for line in output.splitlines()) <= 80, command


def test_mcp_serve_respects_the_off_switch(machine: FakeSystem) -> None:
    run("config", "set", "mcp.enabled", "false")
    code, output = run("mcp", "serve")
    assert code == 1
    assert "MCP is switched off" in output


def test_mcp_serve_home_needs_a_token(machine: FakeSystem) -> None:
    code, output = run("mcp", "serve", "--home")
    assert code == 1
    assert "This device has no token for gonksystem." in output
    assert "gonk home pair" in output
