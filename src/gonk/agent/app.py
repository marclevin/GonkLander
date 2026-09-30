"""The agent's HTTP interface: three routes, all authenticated."""

from __future__ import annotations

import json
from typing import Any

from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from gonk import __version__
from gonk.agent.tokens import DeviceStore
from gonk.capabilities.invoke import Gate, NotAllowed
from gonk.capabilities.registry import Context
from gonk.core.config import Config
from gonk.core.errors import GonkError
from gonk.core.system import System

MAX_BODY_BYTES = 64 * 1024


async def _read_body(request: Request) -> bytes | None:
    """The request body, or None if it is larger than the agent accepts.

    The size is enforced while reading, so an oversized request is dropped
    without ever being held in memory.
    """
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        return None
    received = bytearray()
    async for chunk in request.stream():
        received.extend(chunk)
        if len(received) > MAX_BODY_BYTES:
            return None
    return bytes(received)


def _error(status: int, message: str, hints: list[str] | None = None) -> JSONResponse:
    return JSONResponse({"error": message, "hints": hints or []}, status_code=status)


def create_app(config: Config, system: System, gate: Gate, devices: DeviceStore) -> Starlette:
    def authenticate(request: Request) -> str | None:
        """The calling device's name, or None. There is no anonymous access."""
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token:
            return None
        return devices.verify(token.strip())

    def unauthorized(request: Request) -> JSONResponse:
        client = request.client.host if request.client else "unknown"
        gate.audit.record_quietly("agent.unauthorized", client=client, path=request.url.path)
        return _error(401, "A valid device token is required.")

    async def whoami(request: Request) -> JSONResponse:
        if (device := authenticate(request)) is None:
            return unauthorized(request)
        return JSONResponse({"device": device, "agent": system.hostname(), "version": __version__})

    async def capabilities(request: Request) -> JSONResponse:
        if authenticate(request) is None:
            return unauthorized(request)
        listed = [
            {
                "name": item.name,
                "tool_name": item.tool_name,
                "description": item.description,
                "risk": item.risk,
                "schema": item.schema(),
            }
            for item in gate.allowed()
        ]
        return JSONResponse({"capabilities": listed})

    async def call(request: Request) -> JSONResponse:
        if (device := authenticate(request)) is None:
            return unauthorized(request)

        body = await _read_body(request)
        if body is None:
            return _error(413, "The request is too large.")
        try:
            payload: Any = json.loads(body) if body else {}
        except json.JSONDecodeError:
            return _error(400, "The request body is not valid JSON.")
        arguments = payload.get("arguments", {}) if isinstance(payload, dict) else None
        if not isinstance(arguments, dict):
            return _error(400, "'arguments' should be an object.")

        context = Context(config=config, system=system, caller=device)
        name = request.path_params["name"]
        try:
            result = await run_in_threadpool(gate.call, context, name, arguments)
        except NotAllowed as error:
            return _error(403, error.message, error.hints)
        except GonkError as error:
            return _error(422, error.message, error.hints)
        except OSError:
            return _error(500, "The agent could not write its audit log, so nothing was done.")
        return JSONResponse({"result": result})

    return Starlette(
        routes=[
            Route("/v1/whoami", whoami, methods=["GET"]),
            Route("/v1/capabilities", capabilities, methods=["GET"]),
            Route("/v1/call/{name}", call, methods=["POST"]),
        ]
    )
