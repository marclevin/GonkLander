"""Where the MCP server's tools actually run: on this machine, or at home."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from gonk.capabilities.invoke import Gate, NotAllowed
from gonk.capabilities.registry import Context
from gonk.home.client import AgentClient


@dataclass(frozen=True)
class ToolInfo:
    tool_name: str  # gonk_machine_status
    capability: str  # machine.status
    description: str
    risk: str
    schema: dict[str, Any]


class Backend(Protocol):
    where: str

    def tools(self) -> list[ToolInfo]: ...

    def call(self, tool_name: str, arguments: dict[str, Any]) -> Any: ...


class LocalBackend:
    def __init__(self, gate: Gate, context: Context) -> None:
        self.gate = gate
        self.context = context
        self.where = context.system.hostname()

    def tools(self) -> list[ToolInfo]:
        return [
            ToolInfo(item.tool_name, item.name, item.description, item.risk, item.schema())
            for item in self.gate.allowed()
        ]

    def call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        item = self.gate.registry.by_tool_name(tool_name)
        # An unknown name still goes through the gate, so that it is refused
        # and recorded the same way as a forbidden one.
        return self.gate.call(self.context, item.name if item else tool_name, arguments)


class HomeBackend:
    """Tools run on the agent at home. Its policy decides what exists."""

    def __init__(self, client: AgentClient) -> None:
        self.client = client
        self.where = client.name
        self._names: dict[str, str] = {}

    def tools(self) -> list[ToolInfo]:
        listed = [
            ToolInfo(
                str(item["tool_name"]),
                str(item["name"]),
                str(item.get("description", "")),
                str(item.get("risk", "sensitive")),
                dict(item.get("schema") or {"type": "object", "properties": {}}),
            )
            for item in self.client.capabilities()
        ]
        self._names = {tool.tool_name: tool.capability for tool in listed}
        return listed

    def call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if tool_name not in self._names:
            self.tools()
        if tool_name not in self._names:
            raise NotAllowed(f"'{tool_name}' is not available on {self.where}.")
        return self.client.call(self._names[tool_name], arguments)
