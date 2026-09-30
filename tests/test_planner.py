"""The decisions that make `gonk land` safe to run twice."""

from __future__ import annotations

import pytest

from gonk.core.errors import GonkError
from gonk.core.platform import Platform
from gonk.tools.catalog import ToolSpec, load_catalog
from gonk.tools.install import execute
from gonk.tools.planner import expand, plan
from gonk.tools.profiles import load_profiles, resolve_tools
from gonk.tools.providers import PROVIDERS, Command

from .fakes import FakeSystem

CATALOG = load_catalog()


def one(tool: str, platform: Platform, system: FakeSystem):  # type: ignore[no-untyped-def]
    (decision,) = [item for item in plan([tool], CATALOG, platform, system) if item.tool == tool]
    return decision


def test_present_and_new_enough_means_do_nothing(ubuntu: Platform) -> None:
    """jq is requested, this is Ubuntu, apt is available, jq is already
    installed at an acceptable version, therefore do nothing."""
    system = FakeSystem(installed={"jq": "jq-1.7.1", "apt-get": "", "sudo": ""})
    decision = one("jq", ubuntu, system)
    assert decision.action == "present"
    assert decision.commands == ()
    assert not decision.changes_machine
    assert "1.7.1" in decision.reason


def test_missing_is_installed_with_the_native_package_manager(ubuntu: Platform) -> None:
    system = FakeSystem(installed={"apt-get": "", "sudo": ""})
    decision = one("jq", ubuntu, system)
    assert (decision.action, decision.provider) == ("install", "apt")
    assert decision.commands[0].argv[:3] == ("apt-get", "install", "-y")
    assert decision.commands[0].argv[-1] == "jq"
    assert decision.refresh is not None
    assert decision.refresh.argv[:2] == ("apt-get", "update")


def test_too_old_is_upgraded(ubuntu: Platform) -> None:
    system = FakeSystem(installed={"node": "v16.20.2", "apt-get": "", "sudo": ""})
    decision = one("node", ubuntu, system)
    assert decision.action == "upgrade"
    assert "16.20.2 is older than 18" in decision.reason


def test_too_old_with_no_way_to_upgrade_is_left_alone(ubuntu: Platform) -> None:
    decision = one("node", ubuntu, FakeSystem(installed={"node": "v16.20.2"}))
    assert decision.action == "present"
    assert not decision.changes_machine
    assert "nothing here can upgrade it" in decision.reason


def test_no_provider_means_unavailable(ubuntu: Platform) -> None:
    decision = one("jq", ubuntu, FakeSystem())
    assert decision.action == "unavailable"
    assert "apt" in decision.reason  # says what was tried
    assert decision.commands == ()


def test_unknown_tool_is_unavailable_not_a_crash(ubuntu: Platform) -> None:
    decision = one("frobnicator", ubuntu, FakeSystem())
    assert decision.action == "unavailable"
    assert "not in the tool catalog" in decision.reason


def test_planning_changes_nothing(ubuntu: Platform) -> None:
    system = FakeSystem(installed={"apt-get": "", "sudo": "", "curl": "curl 8.5.0"})
    plan(resolve_tools("full", load_profiles()), CATALOG, ubuntu, system)
    assert system.changed == []


# --- root ----------------------------------------------------------------------


def test_sudo_is_used_only_when_needed() -> None:
    command = Command(("apt-get", "install", "-y", "jq"), needs_root=True, env=(("A", "1"),))
    assert command.full_argv(as_root=False)[:3] == ["sudo", "env", "A=1"]
    assert command.full_argv(as_root=True) == ["apt-get", "install", "-y", "jq"]


def test_user_level_installs_never_use_sudo(ubuntu: Platform) -> None:
    system = FakeSystem(installed={"npm": "10.0.0", "node": "v22.0.0", "sudo": ""})
    decision = one("codex", ubuntu, system)
    assert decision.provider == "npm"
    assert not decision.needs_root
    assert "sudo" not in decision.commands[0].full_argv(as_root=False)
    assert "--prefix" in decision.commands[0].argv


def test_needing_root_without_sudo_is_reported_up_front(ubuntu: Platform) -> None:
    decision = one("jq", ubuntu, FakeSystem(installed={"apt-get": ""}))
    assert decision.action == "unavailable"
    assert "sudo is not installed" in decision.reason


def test_root_does_not_need_sudo(ubuntu: Platform) -> None:
    decision = one("jq", ubuntu, FakeSystem(installed={"apt-get": ""}, root=True))
    assert decision.action == "install"


# --- platforms -----------------------------------------------------------------


def test_the_same_tool_installs_differently_per_platform() -> None:
    fedora = Platform(os="linux", arch="x86_64", distro="fedora")
    mac = Platform(os="macos", arch="arm64")
    windows = Platform(os="windows", arch="x86_64")
    assert one("jq", fedora, FakeSystem(installed={"dnf": "", "sudo": ""})).provider == "dnf"
    assert one("jq", mac, FakeSystem(installed={"brew": ""})).provider == "brew"
    assert one("jq", windows, FakeSystem(installed={"winget": ""})).provider == "winget"


def test_a_provider_for_another_platform_is_not_used() -> None:
    windows = Platform(os="windows", arch="x86_64")
    # apt-get on PATH (say, under WSL interop) must not tempt Gonk on Windows.
    decision = one("tmux", windows, FakeSystem(installed={"apt-get": "", "sudo": ""}))
    assert decision.action == "unavailable"


def test_scripts_are_fetched_over_https_only(ubuntu: Platform) -> None:
    script = PROVIDERS["script"]
    system = FakeSystem()
    assert script.commands({"url": "https://astral.sh/uv/install.sh"}, system)
    for url in ("http://astral.sh/x.sh", "https://x.sh/a; rm -rf ~", "file:///etc/passwd", ""):
        with pytest.raises(GonkError, match="not a plain https"):
            script.commands({"url": url}, system)
    with pytest.raises(GonkError, match="sh or bash"):
        script.commands({"url": "https://astral.sh/x.sh", "shell": "python"}, system)


def test_the_script_url_is_an_argument_not_part_of_a_shell_string() -> None:
    (command,) = PROVIDERS["script"].commands(
        {"url": "https://astral.sh/uv/install.sh"}, FakeSystem()
    )
    program = command.argv[2]
    assert "astral.sh" not in program
    assert "https://astral.sh/uv/install.sh" in command.argv[3:]


@pytest.mark.parametrize("name", ["-o", "--allow-unauthenticated", "jq; reboot", "a b", ""])
def test_package_names_cannot_smuggle_options(name: str) -> None:
    with pytest.raises(GonkError):
        PROVIDERS["apt"].commands({"packages": [name]}, FakeSystem())


# --- dependencies --------------------------------------------------------------


def test_requirements_are_added_before_what_needs_them() -> None:
    order = expand(["codex"], CATALOG)
    assert order.index("node") < order.index("npm") < order.index("codex")


def test_requirement_loops_are_reported() -> None:
    catalog = {"a": ToolSpec("a", requires=("b",)), "b": ToolSpec("b", requires=("a",))}
    with pytest.raises(GonkError, match="loop: a → b → a"):
        expand(["a"], catalog)


def test_a_provider_installed_earlier_in_the_plan_can_be_used(ubuntu: Platform) -> None:
    """npm is not here yet, but the plan installs it first, so codex can use it."""
    system = FakeSystem(installed={"apt-get": "", "sudo": ""})
    decisions = {item.tool: item for item in plan(["codex"], CATALOG, ubuntu, system)}
    assert decisions["npm"].action == "install"
    assert (decisions["codex"].action, decisions["codex"].provider) == ("install", "npm")


# --- carrying it out -------------------------------------------------------------


def ubuntu_box() -> FakeSystem:
    return FakeSystem(
        installed={"apt-get": "", "sudo": "", "git": "git version 2.43.0", "curl": "curl 8.5.0"},
        packages={
            "jq": ["jq"],
            "ripgrep": ["rg"],
            "tmux": ["tmux"],
            "openssh-client": ["ssh"],
        },
    )


def test_landing_twice_does_nothing_the_second_time(ubuntu: Platform) -> None:
    system = ubuntu_box()
    tools = resolve_tools("minimal", load_profiles())

    first = plan(tools, CATALOG, ubuntu, system)
    assert {item.tool for item in first if item.changes_machine} == {"ssh", "jq", "ripgrep", "tmux"}
    outcomes = execute(first, CATALOG, system)
    assert all(outcome.ok for outcome in outcomes)
    ran_first_time = len(system.changed)
    assert ran_first_time > 0

    second = plan(tools, CATALOG, ubuntu, system)
    assert all(item.action == "present" for item in second)
    assert execute(second, CATALOG, system) == []
    assert len(system.changed) == ran_first_time  # nothing new was run


def test_the_package_index_is_refreshed_once(ubuntu: Platform) -> None:
    system = ubuntu_box()
    execute(plan(["jq", "ripgrep", "tmux"], CATALOG, ubuntu, system), CATALOG, system)
    updates = [argv for argv in system.changed if "update" in argv]
    assert len(updates) == 1
    assert system.changed[0] == updates[0]  # and before the first install


def test_present_tools_are_never_reinstalled(ubuntu: Platform) -> None:
    system = ubuntu_box()
    execute(plan(["git", "jq"], CATALOG, ubuntu, system), CATALOG, system)
    assert not any("git" in argv for argv in system.changed)


def test_a_failed_command_is_a_failed_install(ubuntu: Platform) -> None:
    system = ubuntu_box()
    system.exit_code = 100
    (outcome,) = execute(plan(["jq"], CATALOG, ubuntu, system), CATALOG, system)
    assert not outcome.ok
    assert "exited with 100" in outcome.message


def test_exit_zero_is_not_trusted_if_the_tool_is_still_missing(ubuntu: Platform) -> None:
    system = FakeSystem(installed={"apt-get": "", "sudo": ""})  # installs provide nothing
    (outcome,) = execute(plan(["jq"], CATALOG, ubuntu, system), CATALOG, system)
    assert not outcome.ok
    assert "not on PATH" in outcome.message


def test_what_depends_on_a_failure_is_skipped(ubuntu: Platform) -> None:
    system = FakeSystem(installed={"apt-get": "", "sudo": ""})
    outcomes = {
        o.tool: o for o in execute(plan(["codex"], CATALOG, ubuntu, system), CATALOG, system)
    }
    assert not outcomes["node"].ok
    assert "skipped" in outcomes["npm"].message
    assert "skipped" in outcomes["codex"].message
    assert not any("@openai/codex" in argv for argv in system.changed)


def test_post_install_notes_are_passed_on(ubuntu: Platform) -> None:
    system = FakeSystem(installed={"apt-get": "", "sudo": ""}, packages={"gh": ["gh"]})
    (outcome,) = execute(plan(["gh"], CATALOG, ubuntu, system), CATALOG, system)
    assert outcome.ok
    assert "gh auth login" in outcome.note
