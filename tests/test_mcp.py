"""The MCP tool surface, exercised through a real MCP client session."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import anyio
from mcp.shared.memory import create_connected_server_and_client_session
from starlette.testclient import TestClient

from gonk.agent.app import create_app
from gonk.agent.tokens import DeviceStore
from gonk.capabilities.invoke import Gate
from gonk.capabilities.loader import build_registry
from gonk.capabilities.registry import Context
from gonk.core.audit import AuditLog
from gonk.core.config import Config
from gonk.home.client import AgentClient
from gonk.mcp.backends import Backend, HomeBackend, LocalBackend
from gonk.mcp.server import build_server
from gonk.policy import Policy

from .fakes import FakeSystem


def local(config: Config, tmp_path: Path, *allow: str, system: FakeSystem | None = None) -> Backend:
    system = system or FakeSystem(hostname="laptop", installed={"df": ""})
    gate = Gate(build_registry(None), Policy(allow=allow), AuditLog(tmp_path / "audit.log"), "mcp")
    return LocalBackend(gate, Context(config, system, caller="mcp"))


def session(backend: Backend, work: Any) -> Any:
    async def run() -> Any:
        async with create_connected_server_and_client_session(build_server(backend)) as client:
            return await work(client)

    return anyio.run(run)


def tools(backend: Backend) -> dict[str, Any]:
    async def work(client: Any) -> Any:
        return (await client.list_tools()).tools

    return {tool.name: tool for tool in session(backend, work)}


def call(backend: Backend, name: str, arguments: dict[str, Any] | None = None) -> Any:
    async def work(client: Any) -> Any:
        return await client.call_tool(name, arguments or {})

    return session(backend, work)


def test_by_default_only_safe_tools_are_offered(config: Config, tmp_path: Path) -> None:
    offered = tools(local(config, tmp_path))
    assert "gonk_machine_status" in offered
    assert "gonk_status" in offered
    assert not {"gonk_files_read", "gonk_services_restart", "gonk_commands_run"} & set(offered)
    assert all(tool.annotations.readOnlyHint for tool in offered.values())


def test_tool_names_suit_strict_clients(config: Config, tmp_path: Path) -> None:
    import re

    for name in tools(local(config, tmp_path, "*", "commands.run", "services.restart")):
        assert re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", name), name


def test_allowing_a_tool_makes_it_appear(config: Config, tmp_path: Path) -> None:
    offered = tools(local(config, tmp_path, "files.read", "commands.run"))
    assert "gonk_files_read" in offered
    assert "gonk_commands_run" in offered
    assert offered["gonk_commands_run"].annotations.destructiveHint is True
    assert offered["gonk_commands_run"].annotations.readOnlyHint is False
    assert offered["gonk_files_read"].annotations.readOnlyHint is None  # no promise made
    assert "risk: dangerous" in offered["gonk_commands_run"].description


def test_tools_describe_their_arguments(config: Config, tmp_path: Path) -> None:
    schema = tools(local(config, tmp_path))["gonk_projects_status"].inputSchema
    assert schema["required"] == ["name"]
    assert schema["properties"]["name"]["type"] == "string"


def test_calling_a_tool(config: Config, tmp_path: Path) -> None:
    result = call(local(config, tmp_path), "gonk_status")
    assert not result.isError
    assert '"hostname": "laptop"' in result.content[0].text


def test_a_hidden_tool_cannot_be_called_by_guessing_its_name(
    config: Config, tmp_path: Path
) -> None:
    config.commands = {"disk": ["df"]}
    system = FakeSystem(installed={"df": ""})
    backend = local(config, tmp_path, system=system)
    for name in ("gonk_commands_run", "commands.run", "gonk_files_read", "gonk_shell"):
        result = call(backend, name, {"name": "disk", "path": "/etc/passwd"})
        assert result.isError
        assert "is not available" in result.content[0].text
    assert system.ran == []


def test_errors_are_written_for_the_reader(config: Config, tmp_path: Path) -> None:
    result = call(local(config, tmp_path, "files.read"), "gonk_files_read", {"path": "/etc/passwd"})
    assert result.isError
    text = result.content[0].text
    assert "No directories are open for reading" in text
    assert "files.roots" in text  # the hint comes along
    assert "Traceback" not in text


def test_bad_arguments_are_refused(config: Config, tmp_path: Path) -> None:
    result = call(local(config, tmp_path), "gonk_machine_processes", {"limit": "many"})
    assert result.isError
    assert "should be a whole number" in result.content[0].text


def test_mcp_calls_are_recorded(config: Config, tmp_path: Path) -> None:
    backend = local(config, tmp_path)
    call(backend, "gonk_status")
    call(backend, "gonk_files_read", {"path": "/x"})
    events = AuditLog(tmp_path / "audit.log").tail()
    assert [(event["event"], event["interface"]) for event in events] == [
        ("capability.call", "mcp"),
        ("capability.denied", "mcp"),
    ]


# --- tools served from home ------------------------------------------------------


def home(config: Config, tmp_path: Path, *allow: str, token: str | None = None) -> HomeBackend:
    """An MCP backend talking to an in-process agent that plays gonksystem."""
    devices = DeviceStore(tmp_path / "home-config")
    issued = devices.create("laptop")
    gate = Gate(
        build_registry(None), Policy(allow=allow), AuditLog(tmp_path / "home-audit.log"), "agent"
    )
    app = create_app(config, FakeSystem(hostname="gonksystem"), gate, devices)
    client = AgentClient(
        "gonksystem", 4665, token or issued, name="gonksystem", http=TestClient(app)
    )
    return HomeBackend(client)


def test_home_tools_are_the_ones_home_allows(config: Config, tmp_path: Path) -> None:
    offered = tools(home(config, tmp_path))
    assert "gonk_machine_status" in offered
    assert "gonk_files_read" not in offered
    assert "runs on gonksystem" in offered["gonk_machine_status"].description
    assert "gonk_files_read" in tools(home(config, tmp_path / "second", "files.read"))


def test_home_tools_run_at_home(config: Config, tmp_path: Path) -> None:
    result = call(home(config, tmp_path), "gonk_status")
    assert not result.isError
    assert '"hostname": "gonksystem"' in result.content[0].text
    event = AuditLog(tmp_path / "home-audit.log").tail()[-1]
    assert (event["caller"], event["interface"]) == ("laptop", "agent")


def test_home_policy_cannot_be_bypassed_from_the_client(config: Config, tmp_path: Path) -> None:
    backend = home(config, tmp_path)
    assert call(backend, "gonk_files_read", {"path": "/etc/passwd"}).isError
    # Even a client that lies about what exists is refused by the agent itself.
    backend._names["gonk_files_read"] = "files.read"
    result = call(backend, "gonk_files_read", {"path": "/etc/passwd"})
    assert result.isError
    assert "is not available" in result.content[0].text


def test_a_rejected_token_is_explained(config: Config, tmp_path: Path) -> None:
    result = call(home(config, tmp_path, token="gonk_wrong"), "gonk_status")
    assert result.isError
    assert "did not accept this device's token" in result.content[0].text
    assert "gonk home pair" in result.content[0].text
