from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from gonk.capabilities import CapabilityError, Context, Registry, capability
from gonk.capabilities.builtin import files
from gonk.capabilities.loader import build_registry, load_plugins
from gonk.capabilities.registry import MARK
from gonk.core.config import Config
from gonk.core.errors import GonkError
from gonk.core.system import Result

from .fakes import FakeSystem


def registered(function: Any) -> Any:
    return getattr(function, MARK)


# --- declaring -----------------------------------------------------------------


def test_a_capability_describes_its_parameters() -> None:
    @capability("demo.greet", risk="safe", description="Say hello.")
    def greet(ctx: Context, name: str, times: int = 1, loud: bool = False) -> str:
        return name * times

    schema = registered(greet).schema()
    assert schema["required"] == ["name"]
    assert schema["properties"]["name"] == {"type": "string"}
    assert schema["properties"]["times"] == {"type": "integer", "default": 1}
    assert schema["properties"]["loud"] == {"type": "boolean", "default": False}
    assert schema["additionalProperties"] is False


def test_risk_defaults_to_sensitive() -> None:
    @capability("demo.unrated", description="Forgot to say how risky.")
    def unrated(ctx: Context) -> None: ...

    assert registered(unrated).risk == "sensitive"


@pytest.mark.parametrize(
    "name", ["status", "Machine.status", "machine..status", "a.b-c", "1a.b", ""]
)
def test_names_must_be_dotted_lowercase(name: str) -> None:
    with pytest.raises(GonkError, match="not a valid capability name"):
        capability(name, description="x")


def test_risk_must_be_known() -> None:
    with pytest.raises(GonkError, match="risk must be one of"):
        capability("demo.x", description="x", risk="harmless")  # type: ignore[arg-type]


def test_parameters_must_have_simple_types() -> None:
    with pytest.raises(GonkError, match="'items' must be annotated"):

        @capability("demo.bad", description="x")
        def bad(ctx: Context, items: list[str]) -> None: ...


def test_tool_names_are_safe_for_mcp_clients() -> None:
    @capability("machine.status", risk="safe", description="x")
    def one(ctx: Context) -> None: ...

    @capability("gonk.status", risk="safe", description="x")
    def two(ctx: Context) -> None: ...

    assert registered(one).tool_name == "gonk_machine_status"
    assert registered(two).tool_name == "gonk_status"  # not gonk_gonk_status


# --- calling -------------------------------------------------------------------


def demo() -> Any:
    @capability("demo.add", risk="safe", description="Add.")
    def add(ctx: Context, a: int, b: int = 1, ratio: float = 1.0) -> float:
        return (a + b) * ratio

    return registered(add)


def test_arguments_are_checked_not_coerced(config: Config, system: FakeSystem) -> None:
    context = Context(config, system)
    assert demo().call(context, {"a": 2}) == 3
    assert demo().call(context, {"a": 2, "b": 3, "ratio": 2}) == 10  # an int is a fine float

    for arguments, message in [
        ({}, "needs 'a'"),
        ({"a": "2"}, "'a' should be a whole number"),
        ({"a": True}, "'a' should be a whole number"),
        ({"a": 1.5}, "'a' should be a whole number"),
        ({"a": 1, "c": 1}, "does not take 'c'"),
        ({"a": 1, "b": None}, "'b' should be a whole number"),
    ]:
        with pytest.raises(CapabilityError, match=message):
            demo().call(context, arguments)


# --- registry ------------------------------------------------------------------


def test_builtin_capabilities_load() -> None:
    names = {item.name for item in build_registry(None).all()}
    assert {
        "gonk.status", "machine.status", "machine.processes", "machine.services",
        "projects.list", "projects.status", "tools.list",
        "files.read", "services.status", "services.restart", "commands.run",
    } <= names  # fmt: skip


def test_builtin_risk_levels() -> None:
    risks = {item.name: item.risk for item in build_registry(None).all()}
    assert risks["machine.status"] == "safe"
    assert risks["files.read"] == "sensitive"
    assert risks["services.restart"] == "dangerous"
    assert risks["commands.run"] == "dangerous"


def test_nothing_takes_a_command_line() -> None:
    """There is no capability whose parameters could carry a shell command."""
    for item in build_registry(None).all():
        for parameter in item.parameters:
            assert parameter.name not in {"command", "cmd", "argv", "args", "shell", "script"}, (
                f"{item.name} takes '{parameter.name}'"
            )


def test_duplicate_names_are_refused() -> None:
    registry = Registry()
    registry.add(demo())
    with pytest.raises(GonkError, match="defined twice"):
        registry.add(demo())


def test_names_that_collide_over_mcp_are_refused() -> None:
    @capability("a.b_c", description="x")
    def first(ctx: Context) -> None: ...

    @capability("a_b.c", description="x")
    def second(ctx: Context) -> None: ...

    registry = Registry()
    registry.add(registered(first))
    with pytest.raises(GonkError, match="would both be the MCP tool gonk_a_b_c"):
        registry.add(registered(second))


# --- plugins -------------------------------------------------------------------

PLUGIN = """
from gonk.capabilities import Context, capability

@capability("mphil.sync_calendar", description="Sync the calendar.")
def sync(ctx: Context, days: int = 7) -> dict:
    return {"synced_days": days}

@capability("mphil.ping", risk="safe", description="Ping.")
def ping(ctx: Context) -> str:
    return "pong"
"""


def plugin_directory(tmp_path: Path, **plugins: str) -> Path:
    directory = tmp_path / "config" / "plugins"
    directory.mkdir(parents=True, mode=0o700)
    for name, text in plugins.items():
        path = directory / f"{name}.py"
        path.write_text(text)
        path.chmod(0o600)
    return directory


def test_a_plugin_is_one_file(tmp_path: Path, config: Config, system: FakeSystem) -> None:
    plugin_directory(tmp_path, mphil=PLUGIN)
    registry = build_registry(tmp_path / "config")
    assert registry.problems == []

    sync = registry.get("mphil.sync_calendar")
    assert sync is not None
    assert sync.source == "plugin mphil.py"
    assert sync.risk == "sensitive"  # unrated plugins start switched off
    assert sync.tool_name == "gonk_mphil_sync_calendar"
    assert sync.call(Context(config, system), {"days": 3}) == {"synced_days": 3}


def test_a_broken_plugin_is_reported_and_the_rest_still_load(tmp_path: Path) -> None:
    plugin_directory(tmp_path, broken="raise RuntimeError('boom')", mphil=PLUGIN)
    registry = build_registry(tmp_path / "config")
    assert registry.get("mphil.ping") is not None
    assert registry.get("machine.status") is not None
    assert registry.problems == ["broken.py: boom"]


def test_a_plugin_cannot_replace_a_builtin(tmp_path: Path) -> None:
    hijack = PLUGIN.replace("mphil.ping", "machine.status")
    plugin_directory(tmp_path, hijack=hijack)
    registry = build_registry(tmp_path / "config")
    assert registry.capabilities["machine.status"].source == "built-in"
    assert any("defined twice" in problem for problem in registry.problems)


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX permissions")
def test_a_plugin_others_can_write_is_not_loaded(tmp_path: Path) -> None:
    directory = plugin_directory(tmp_path, mphil=PLUGIN)
    (directory / "mphil.py").chmod(0o666)
    registry = Registry()
    load_plugins(registry, directory)
    assert registry.all() == []
    assert "writable by other users" in registry.problems[0]


@pytest.mark.skipif(not hasattr(os, "getuid"), reason="POSIX permissions")
def test_a_plugin_directory_others_can_write_is_not_loaded(tmp_path: Path) -> None:
    directory = plugin_directory(tmp_path, mphil=PLUGIN)
    directory.chmod(0o777)
    registry = Registry()
    load_plugins(registry, directory)
    assert registry.all() == []
    assert "no plugins were loaded" in registry.problems[0]


# --- files.read ----------------------------------------------------------------


@pytest.fixture
def open_root(tmp_path: Path, config: Config) -> Path:
    root = tmp_path / "open"
    root.mkdir()
    (root / "notes.txt").write_text("hello")
    (tmp_path / "private").mkdir()
    (tmp_path / "private" / "diary.txt").write_text("secret thoughts")
    config.files.roots = [str(root)]
    return root


def read(config: Config, system: FakeSystem, path: str) -> dict[str, Any]:
    return files.read(Context(config, system), path)  # type: ignore[no-any-return]


def test_reads_inside_an_open_directory(
    config: Config, system: FakeSystem, open_root: Path
) -> None:
    result = read(config, system, str(open_root / "notes.txt"))
    assert result["content"] == "hello"
    assert result["truncated"] is False


def test_nothing_is_readable_by_default(config: Config, system: FakeSystem, tmp_path: Path) -> None:
    (tmp_path / "file.txt").write_text("x")
    with pytest.raises(CapabilityError, match="No directories are open"):
        read(config, system, str(tmp_path / "file.txt"))


def test_refuses_outside_the_open_directory(
    config: Config, system: FakeSystem, open_root: Path, tmp_path: Path
) -> None:
    with pytest.raises(CapabilityError, match="outside the directories open"):
        read(config, system, str(tmp_path / "private" / "diary.txt"))


def test_refuses_to_climb_out_with_dot_dot(
    config: Config, system: FakeSystem, open_root: Path
) -> None:
    with pytest.raises(CapabilityError, match="outside the directories open"):
        read(config, system, str(open_root / ".." / "private" / "diary.txt"))


def test_refuses_a_symlink_that_leads_outside(
    config: Config, system: FakeSystem, open_root: Path, tmp_path: Path
) -> None:
    (open_root / "shortcut").symlink_to(tmp_path / "private" / "diary.txt")
    (open_root / "folder").symlink_to(tmp_path / "private")
    for path in (open_root / "shortcut", open_root / "folder" / "diary.txt"):
        with pytest.raises(CapabilityError, match="outside the directories open"):
            read(config, system, str(path))


def test_a_sibling_with_a_similar_name_is_outside(
    config: Config, system: FakeSystem, open_root: Path, tmp_path: Path
) -> None:
    (tmp_path / "open-but-not").mkdir()
    (tmp_path / "open-but-not" / "x.txt").write_text("x")
    with pytest.raises(CapabilityError, match="outside"):
        read(config, system, str(tmp_path / "open-but-not" / "x.txt"))


@pytest.mark.parametrize(
    "name",
    [".env", ".env.production", "server.pem", "id_ed25519", "id_rsa.pub", "credentials.yaml",
     ".netrc", "private.key", "api.token", ".npmrc"],
)  # fmt: skip
def test_refuses_files_that_look_like_credentials(
    config: Config, system: FakeSystem, open_root: Path, name: str
) -> None:
    (open_root / name).write_text("hunter2")
    with pytest.raises(CapabilityError, match="looks like it holds credentials"):
        read(config, system, str(open_root / name))


def test_refuses_credential_directories(
    config: Config, system: FakeSystem, open_root: Path
) -> None:
    (open_root / ".ssh").mkdir()
    (open_root / ".ssh" / "config").write_text("Host *")
    with pytest.raises(CapabilityError, match="credentials"):
        read(config, system, str(open_root / ".ssh" / "config"))


def test_gonks_own_files_are_never_readable(
    config: Config, system: FakeSystem, tmp_path: Path
) -> None:
    """Even when the owner opens a directory that contains them."""
    config.files.roots = [str(tmp_path)]
    for directory, name in (
        (config.directory, "devices.json"),
        (config.state_directory, "audit.log"),
    ):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / name).write_text("{}")
        with pytest.raises(CapabilityError, match="credentials"):
            read(config, system, str(directory / name))


def test_git_internals_are_not_readable(
    config: Config, system: FakeSystem, open_root: Path
) -> None:
    (open_root / ".git").mkdir()
    (open_root / ".git" / "config").write_text("url = https://token@github.com/x/y")
    with pytest.raises(CapabilityError, match="credentials"):
        read(config, system, str(open_root / ".git" / "config"))


def test_large_files_are_cut_short(config: Config, system: FakeSystem, open_root: Path) -> None:
    config.files.max_bytes = 10
    (open_root / "big.txt").write_text("x" * 100)
    result = read(config, system, str(open_root / "big.txt"))
    assert result["truncated"] is True
    assert len(result["content"]) == 10
    assert result["size_bytes"] == 100


def test_refuses_binary_and_missing_and_directories(
    config: Config, system: FakeSystem, open_root: Path
) -> None:
    (open_root / "image.bin").write_bytes(b"\x89PNG\x00\x00")
    with pytest.raises(CapabilityError, match="binary"):
        read(config, system, str(open_root / "image.bin"))
    with pytest.raises(CapabilityError, match="does not exist"):
        read(config, system, str(open_root / "ghost.txt"))
    with pytest.raises(CapabilityError, match="not a file"):
        read(config, system, str(open_root))


# --- the other guards ------------------------------------------------------------


def call(capability_name: str, config: Config, system: FakeSystem, /, **arguments: Any) -> Any:
    item = build_registry(None).get(capability_name)
    assert item is not None
    return item.call(Context(config, system), arguments)


def test_services_must_be_listed_by_the_owner(config: Config) -> None:
    system = FakeSystem(installed={"systemctl": ""})
    for name in ("services.status", "services.restart"):
        with pytest.raises(CapabilityError, match=r"not in services\.allowed"):
            call(name, config, system, name="nginx.service")
    assert system.ran == []


def test_service_status(config: Config) -> None:
    config.services.allowed = ["nginx.service", "user:gonk-agent.service"]
    shown = Result(0, "Description=web\nActiveState=active\nSubState=running\nMainPID=42\n")
    system = FakeSystem(installed={"systemctl": ""}, results={"systemctl": shown})

    report = call("services.status", config, system, name="nginx.service")
    assert (report["active"], report["state"], report["pid"]) == ("active", "running", "42")
    assert system.ran[-1][:3] == ["systemctl", "show", "nginx.service"]

    call("services.status", config, system, name="user:gonk-agent.service")
    assert system.ran[-1][:4] == ["systemctl", "--user", "show", "gonk-agent.service"]


def test_service_restart_never_uses_sudo(config: Config) -> None:
    config.services.allowed = ["nginx.service"]
    system = FakeSystem(installed={"systemctl": "", "sudo": ""}, results={"systemctl": Result(0)})
    call("services.restart", config, system, name="nginx.service")
    assert ["systemctl", "restart", "nginx.service"] in system.ran
    assert not any(argv[0] == "sudo" for argv in system.ran)


def test_commands_run_by_name_only(config: Config) -> None:
    config.commands = {"disk": ["df", "-h"]}
    system = FakeSystem(installed={"df": ""}, results={"df -h": Result(0, "Filesystem\n")})
    report = call("commands.run", config, system, name="disk")
    assert report["exit_code"] == 0
    assert system.ran == [["df", "-h"]]

    for attempt in ("df -h /", "disk; reboot", "rm", "../disk"):
        with pytest.raises(CapabilityError, match="no command called"):
            call("commands.run", config, system, name=attempt)
    assert system.ran == [["df", "-h"]]  # nothing else was ever run


def test_processes_never_asks_for_command_lines(config: Config) -> None:
    listing = "  1 root  12000  0.0 systemd\n 77 marc 900000  3.5 /usr/lib/firefox/firefox\n"
    system = FakeSystem(installed={"ps": ""}, results={"ps": Result(0, listing)})
    rows = call("machine.processes", config, system, limit=5)
    assert [row["name"] for row in rows] == ["firefox", "systemd"]
    assert rows[0]["memory_mib"] == pytest.approx(878.9, abs=0.1)
    requested = " ".join(system.ran[0])
    assert "comm" in requested
    assert "args" not in requested
    assert "cmd" not in requested


def test_projects_are_found_by_name_never_by_path(config: Config, tmp_path: Path) -> None:
    root = tmp_path / "Code"
    (root / "alpha" / ".git").mkdir(parents=True)
    (root / "not-a-repo").mkdir()
    config.projects.roots = [str(root)]
    status = Result(0, "## main...origin/main\n M README.md\n?? new.txt\n")
    log = Result(0, "abc1234\t2 hours ago\tFix the thing\n")
    system = FakeSystem(installed={"git": ""}, results={"git": status})
    system.results[
        "git -c core.fsmonitor=false -c core.hooksPath=/dev/null -C " + str(root / "alpha") + " log"
    ] = log

    assert [item["name"] for item in call("projects.list", config, system)] == ["alpha"]

    report = call("projects.status", config, system, name="alpha")
    assert report["branch"] == "main...origin/main"
    assert (report["clean"], report["changed_files"]) == (False, 2)
    assert report["last_commit"]["subject"] == "Fix the thing"

    for attempt in ("../alpha", str(root / "alpha"), "/etc", "not-a-repo"):
        with pytest.raises(CapabilityError, match="no project called"):
            call("projects.status", config, system, name=attempt)


def test_machine_status(config: Config) -> None:
    system = FakeSystem(
        files={
            "/proc/uptime": "93784.12 1000.0\n",
            "/proc/meminfo": "MemTotal: 16000000 kB\nMemAvailable: 4000000 kB\n",
        }
    )
    report = call("machine.status", config, system)
    assert report["uptime"] == "1d 2h"
    assert report["memory"]["used_percent"] == 75.0
    assert report["disk"]["root"]["used_percent"] == 60.0
