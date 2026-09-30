"""Expose a backend's tools over the Model Context Protocol."""

from __future__ import annotations

import json
from typing import Any

import anyio
import mcp.server.stdio
import mcp.types as types
from mcp.server.lowlevel import Server

from gonk import __version__
from gonk.mcp.backends import Backend, ToolInfo

INSTRUCTIONS = (
    "Gonk gives you a small set of tools for looking at Marc's machines and projects. "
    "Each tool does one fixed thing; there is no general shell. "
    "If a tool you expect is missing, it has not been allowed in Gonk's policy."
)


def annotations_for(tool: ToolInfo) -> types.ToolAnnotations:
    """Tell the client how careful to be. Only what the risk level actually
    guarantees is claimed: safe capabilities are read-only by definition."""
    if tool.risk == "safe":
        return types.ToolAnnotations(title=tool.capability, readOnlyHint=True)
    if tool.risk == "dangerous":
        return types.ToolAnnotations(
            title=tool.capability, readOnlyHint=False, destructiveHint=True
        )
    return types.ToolAnnotations(title=tool.capability)


def describe(tool: ToolInfo, where: str) -> types.Tool:
    return types.Tool(
        name=tool.tool_name,
        description=f"{tool.description} (runs on {where}; risk: {tool.risk})",
        inputSchema=tool.schema,
        annotations=annotations_for(tool),
    )


def build_server(backend: Backend) -> Server[Any, Any]:
    server: Server[Any, Any] = Server("gonk", version=__version__, instructions=INSTRUCTIONS)

    @server.list_tools()  # type: ignore[no-untyped-call,untyped-decorator]
    async def list_tools() -> list[types.Tool]:
        tools = await anyio.to_thread.run_sync(backend.tools)
        return [describe(tool, backend.where) for tool in tools]

    # Arguments are validated by the capability itself, in one place, whether
    # the call arrives over MCP or through the agent.
    #
    # A GonkError raised here reaches the client as an error result carrying
    # str(error): the message and its hints, which the model can pass on.
    @server.call_tool(validate_input=False)  # type: ignore[untyped-decorator]
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[types.TextContent]:
        try:
            result = await anyio.to_thread.run_sync(backend.call, name, arguments or {})
        except OSError:
            raise RuntimeError("Gonk could not write its audit log, so nothing was done.") from None
        return [types.TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

    return server


async def serve_stdio(backend: Backend) -> None:
    server = build_server(backend)
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())
