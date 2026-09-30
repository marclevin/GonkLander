"""Configuration: defaults, then config.yaml, then environment variables."""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from gonk.core import paths
from gonk.core.errors import GonkError
from gonk.core.system import System

CONFIG_FILE = "config.yaml"
CREDENTIALS_FILE = "credentials.yaml"
TOKEN_VARIABLE = "GONK_HOME_TOKEN"  # noqa: S105 - the name of a variable, not a secret


@dataclass
class Home:
    name: str = "gonksystem"
    transport: str = "tailscale"
    host: str = "gonksystem"
    user: str = ""
    ssh_port: int = 22
    agent_port: int = 4665
    tmux_session: str = "gonk"
    code_path: str = ""


@dataclass
class Lander:
    default_profile: str = "dev"


@dataclass
class Agent:
    enabled: bool = True
    bind: str = "127.0.0.1"
    port: int = 4665
    allow_public_bind: bool = False


@dataclass
class Mcp:
    enabled: bool = True


@dataclass
class PolicySettings:
    allow: list[str] = field(default_factory=list)
    deny: list[str] = field(default_factory=list)


@dataclass
class Projects:
    roots: list[str] = field(default_factory=lambda: ["~/Code"])


@dataclass
class Files:
    roots: list[str] = field(default_factory=list)
    max_bytes: int = 200_000


@dataclass
class Services:
    allowed: list[str] = field(default_factory=list)


@dataclass
class Config:
    home: Home = field(default_factory=Home)
    lander: Lander = field(default_factory=Lander)
    agent: Agent = field(default_factory=Agent)
    mcp: Mcp = field(default_factory=Mcp)
    policy: PolicySettings = field(default_factory=PolicySettings)
    projects: Projects = field(default_factory=Projects)
    files: Files = field(default_factory=Files)
    services: Services = field(default_factory=Services)
    commands: dict[str, list[str]] = field(default_factory=dict)

    # Not settings: where this configuration came from.
    directory: Path = Path()
    state_directory: Path = Path()
    exists: bool = False
    warnings: list[str] = field(default_factory=list)

    @property
    def path(self) -> Path:
        return self.directory / CONFIG_FILE

    def settings(self) -> dict[str, Any]:
        """The settings as plain data, for display."""
        data: dict[str, Any] = {name: dataclasses.asdict(getattr(self, name)) for name in SECTIONS}
        data["commands"] = dict(self.commands)
        return data


SECTIONS = ("home", "lander", "agent", "mcp", "policy", "projects", "files", "services")

TRUE_WORDS = {"1", "true", "yes", "on"}
FALSE_WORDS = {"0", "false", "no", "off"}


def _coerce(value: Any, example: Any, where: str) -> Any:
    """Make `value` the same type as `example`, or explain why it cannot be."""
    if isinstance(example, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.lower() in TRUE_WORDS | FALSE_WORDS:
            return value.lower() in TRUE_WORDS
        raise GonkError(f"{where} should be true or false, not {value!r}.")
    if isinstance(example, int):
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lstrip("-").isdigit():
            return int(value)
        raise GonkError(f"{where} should be a whole number, not {value!r}.")
    if isinstance(example, list):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        if isinstance(value, list) and all(isinstance(item, str) for item in value):
            return list(value)
        raise GonkError(f"{where} should be a list of text values, not {value!r}.")
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return str(value)
    raise GonkError(f"{where} should be text, not {value!r}.")


def _commands(value: Any) -> dict[str, list[str]]:
    message = (
        "commands should map a name to a list of words, for example\n"
        "    commands:\n"
        "      uptime: [uptime, -p]"
    )
    if not isinstance(value, dict):
        raise GonkError(message)
    commands: dict[str, list[str]] = {}
    for name, argv in value.items():
        if not (isinstance(argv, list) and argv and all(isinstance(word, str) for word in argv)):
            raise GonkError(message)
        commands[str(name)] = list(argv)
    return commands


def build(raw: Mapping[str, Any], env: Mapping[str, str]) -> Config:
    """Combine defaults, file contents and environment. Pure."""
    config = Config()
    for key, value in raw.items():
        if key == "commands":
            config.commands = _commands(value)
            continue
        if key not in SECTIONS:
            config.warnings.append(f"unknown section '{key}' is ignored")
            continue
        if not isinstance(value, dict):
            raise GonkError(f"{key} should be a section with settings under it, not {value!r}.")
        section = getattr(config, key)
        known = {item.name for item in dataclasses.fields(section)}
        for name, item in value.items():
            if name not in known:
                config.warnings.append(f"unknown setting '{key}.{name}' is ignored")
                continue
            setattr(section, name, _coerce(item, getattr(section, name), f"{key}.{name}"))

    for key in SECTIONS:
        section = getattr(config, key)
        for item in dataclasses.fields(section):
            variable = f"GONK_{key}_{item.name}".upper()
            if variable in env:
                setattr(
                    section,
                    item.name,
                    _coerce(env[variable], getattr(section, item.name), variable),
                )
    return config


def read_yaml(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    except OSError as error:
        raise GonkError(f"Could not read {path}: {error.strerror}.") from error
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as error:
        mark = getattr(error, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark else ""
        raise GonkError(
            f"{path} is not valid YAML{where}.",
            hints=[f"Open {path} and check the indentation near that line."],
        ) from error
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise GonkError(f"{path} should contain settings grouped in sections.")
    return data


def load(system: System) -> Config:
    directory = paths.config_dir(system.env, system.home())
    path = directory / CONFIG_FILE
    try:
        config = build(read_yaml(path), system.env)
    except GonkError as error:
        # Say which file is at fault; the inner message only knows the key.
        if str(path) not in error.message:
            error.message = f"{path}: {error.message}"
        raise
    config.directory = directory
    config.state_directory = paths.state_dir(system.env, system.home())
    config.exists = path.exists()
    return config


def set_value(directory: Path, dotted: str, value: str) -> Any:
    """Change one setting in config.yaml. Returns the value as stored."""
    section_name, _, name = dotted.partition(".")
    defaults = Config()
    if section_name not in SECTIONS or not name:
        raise GonkError(
            f"'{dotted}' is not a setting.",
            hints=["gonk config show    # lists every setting"],
        )
    section = getattr(defaults, section_name)
    if name not in {item.name for item in dataclasses.fields(section)}:
        known = ", ".join(item.name for item in dataclasses.fields(section))
        raise GonkError(f"'{dotted}' is not a setting. {section_name} has: {known}.")
    stored = _coerce(value, getattr(section, name), dotted)

    path = directory / CONFIG_FILE
    raw = read_yaml(path)
    raw.setdefault(section_name, {})[name] = stored
    paths.ensure_directory(directory)
    path.write_text(yaml.safe_dump(raw, sort_keys=False), encoding="utf-8")
    return stored


# --- credentials -----------------------------------------------------------


def home_token(config: Config, env: Mapping[str, str]) -> str:
    """This device's token for the agent at home, or ""."""
    if token := env.get(TOKEN_VARIABLE, "").strip():
        return token
    data = read_yaml(config.directory / CREDENTIALS_FILE)
    token = data.get("home_token", "")
    return token.strip() if isinstance(token, str) else ""


def save_home_token(config: Config, token: str) -> Path:
    path = config.directory / CREDENTIALS_FILE
    paths.write_private(path, yaml.safe_dump({"home_token": token}))
    return path
