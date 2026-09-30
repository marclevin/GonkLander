"""The capability decorator and the registry that holds what it marks."""

from __future__ import annotations

import inspect
import re
import typing
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from gonk.core.config import Config
from gonk.core.errors import GonkError
from gonk.core.system import System

Risk = Literal["safe", "sensitive", "dangerous"]
RISKS: tuple[Risk, ...] = ("safe", "sensitive", "dangerous")

NAME = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")
MARK = "__gonk_capability__"

# Parameter types a capability may declare, and their JSON Schema names.
JSON_TYPES: dict[type, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}
PLAIN_TYPES: dict[type, str] = {
    str: "text",
    int: "a whole number",
    float: "a number",
    bool: "true or false",
}


class CapabilityError(GonkError):
    """The call was understood and refused, or it failed in an expected way."""


@dataclass
class Context:
    """What a capability is given to work with."""

    config: Config
    system: System
    caller: str = "local"


@dataclass(frozen=True)
class Parameter:
    name: str
    kind: type
    required: bool
    default: Any = None


@dataclass(frozen=True)
class Capability:
    name: str
    description: str
    risk: Risk
    handler: Callable[..., Any]
    parameters: tuple[Parameter, ...] = ()
    source: str = "built-in"

    @property
    def tool_name(self) -> str:
        """The name used over MCP, where dots are not universally accepted."""
        flat = self.name.replace(".", "_")
        return flat if flat.startswith("gonk_") else f"gonk_{flat}"

    def schema(self) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        for parameter in self.parameters:
            properties[parameter.name] = {"type": JSON_TYPES[parameter.kind]}
            if not parameter.required:
                properties[parameter.name]["default"] = parameter.default
        return {
            "type": "object",
            "properties": properties,
            "required": [item.name for item in self.parameters if item.required],
            "additionalProperties": False,
        }

    def validate(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Check arguments against the declared parameters. Nothing is coerced."""
        known = {parameter.name: parameter for parameter in self.parameters}
        if unknown := sorted(set(arguments) - set(known)):
            expected = ", ".join(known) or "no arguments"
            raise CapabilityError(
                f"{self.name} does not take '{unknown[0]}'. It takes: {expected}."
            )
        checked: dict[str, Any] = {}
        for parameter in self.parameters:
            if parameter.name not in arguments:
                if parameter.required:
                    raise CapabilityError(f"{self.name} needs '{parameter.name}'.")
                continue
            value = arguments[parameter.name]
            if not _matches(value, parameter.kind):
                raise CapabilityError(
                    f"{self.name}: '{parameter.name}' should be "
                    f"{PLAIN_TYPES[parameter.kind]}, not {value!r}."
                )
            checked[parameter.name] = value
        return checked

    def call(self, context: Context, arguments: dict[str, Any]) -> Any:
        return self.handler(context, **self.validate(arguments))


def _matches(value: Any, kind: type) -> bool:
    if isinstance(value, bool):  # bool is an int in Python; it is not one here
        return kind is bool
    if kind is float:
        return isinstance(value, (int, float))
    return isinstance(value, kind)


def _parameters(handler: Callable[..., Any], name: str) -> tuple[Parameter, ...]:
    signature = inspect.signature(handler)
    hints = typing.get_type_hints(handler)
    declared = list(signature.parameters.values())
    if not declared:
        raise GonkError(f"Capability {name}: the function must take a Context first.")
    parameters = []
    for item in declared[1:]:
        kind = hints.get(item.name)
        if kind not in JSON_TYPES:
            allowed = ", ".join(kind.__name__ for kind in JSON_TYPES)
            raise GonkError(
                f"Capability {name}: parameter '{item.name}' must be annotated as one of {allowed}."
            )
        required = item.default is inspect.Parameter.empty
        parameters.append(Parameter(item.name, kind, required, None if required else item.default))
    return tuple(parameters)


def capability(
    name: str, *, description: str, risk: Risk = "sensitive"
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Mark a function as a capability.

    `risk` defaults to "sensitive", so a capability that forgets to say how
    risky it is stays switched off until policy allows it.
    """
    if not NAME.match(name):
        raise GonkError(
            f"'{name}' is not a valid capability name. Use lowercase words joined by dots, "
            "like 'mphil.sync_calendar'."
        )
    if risk not in RISKS:
        raise GonkError(f"Capability {name}: risk must be one of {', '.join(RISKS)}.")
    if not description.strip():
        raise GonkError(f"Capability {name} needs a description.")

    def mark(handler: Callable[..., Any]) -> Callable[..., Any]:
        setattr(
            handler,
            MARK,
            Capability(name, description.strip(), risk, handler, _parameters(handler, name)),
        )
        return handler

    return mark


@dataclass
class Registry:
    capabilities: dict[str, Capability] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)  # plugins that failed to load

    def add(self, item: Capability) -> None:
        if item.name in self.capabilities:
            existing = self.capabilities[item.name]
            raise GonkError(
                f"Capability {item.name} is defined twice: in {existing.source} "
                f"and in {item.source}."
            )
        for existing in self.capabilities.values():
            if existing.tool_name == item.tool_name:
                raise GonkError(
                    f"Capabilities {existing.name} and {item.name} would both be the MCP tool "
                    f"{item.tool_name}. Rename one of them."
                )
        self.capabilities[item.name] = item

    def add_from(self, namespace: dict[str, Any], source: str) -> int:
        """Register every marked function found in a module's namespace."""
        found = 0
        for value in namespace.values():
            marked = getattr(value, MARK, None)
            if isinstance(marked, Capability) and marked.handler is value:
                self.add(
                    Capability(
                        marked.name,
                        marked.description,
                        marked.risk,
                        marked.handler,
                        marked.parameters,
                        source,
                    )
                )
                found += 1
        return found

    def get(self, name: str) -> Capability | None:
        return self.capabilities.get(name)

    def by_tool_name(self, tool_name: str) -> Capability | None:
        for item in self.capabilities.values():
            if item.tool_name == tool_name:
                return item
        return None

    def all(self) -> list[Capability]:
        return sorted(self.capabilities.values(), key=lambda item: item.name)
