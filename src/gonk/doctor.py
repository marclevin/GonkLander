"""gonk doctor: what is this machine, and what is wrong with it?"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from gonk import __version__
from gonk.agent import service
from gonk.agent.tokens import DeviceStore
from gonk.capabilities.loader import build_registry
from gonk.core import platform as platforms
from gonk.core.config import Config, home_token
from gonk.core.errors import Check, GonkError, Status
from gonk.core.system import System
from gonk.home import status as home_status
from gonk.policy import Policy
from gonk.tools.catalog import ToolSpec, load_catalog
from gonk.tools.detect import detect
from gonk.tools.profiles import load_profiles, resolve_tools

# Shown in this order, whether or not they are in the active profile.
DOCTOR_TOOLS = (
    "git", "python", "uv", "node", "npm", "pnpm", "docker", "gh", "ssh", "tailscale",
    "ripgrep", "jq", "tmux", "claude-code", "codex",
)  # fmt: skip


@dataclass
class Section:
    title: str
    checks: list[Check] = field(default_factory=list)


@dataclass
class Report:
    sections: list[Section] = field(default_factory=list)

    def all_checks(self) -> list[Check]:
        return [check for section in self.sections for check in section.checks]

    def count(self, status: str) -> int:
        return sum(1 for check in self.all_checks() if check.status == status)

    @property
    def healthy(self) -> bool:
        return self.count("fail") == 0

    def as_data(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "sections": [
                {"title": section.title, "checks": [vars(check) for check in section.checks]}
                for section in self.sections
            ],
        }


def system_section(system: System) -> Section:
    machine = platforms.detect(system)
    section = Section("System")
    add = section.checks.append

    add(Check("Hostname", "info", system.hostname()))
    if machine.supported:
        add(Check("OS", "ok", machine.pretty_name + (" (WSL)" if machine.is_wsl else "")))
    else:
        add(Check("OS", "fail", machine.pretty_name, "Gonk supports Linux, macOS and Windows."))
    add(Check("Architecture", "ok", machine.arch))

    shell = system.env.get("SHELL") or system.env.get("COMSPEC") or ""
    add(Check("Shell", "ok" if shell else "warn", shell or "could not tell"))

    manager = platforms.package_manager(machine, system)
    if manager:
        add(Check("Package manager", "ok", manager))
    else:
        add(
            Check(
                "Package manager",
                "warn",
                "none found",
                "Gonk can still install tools that come as scripts or npm packages.",
            )
        )

    local_bin = str(system.home() / ".local" / "bin")
    on_path = local_bin in system.env.get("PATH", "").split(os.pathsep)
    if machine.os != "windows":
        if on_path:
            add(Check("~/.local/bin on PATH", "ok"))
        else:
            add(
                Check(
                    "~/.local/bin on PATH",
                    "warn",
                    "tools installed there will not be found by your shell",
                    'Add to your shell profile: export PATH="$HOME/.local/bin:$PATH"',
                )
            )
    return section


def _tool_extras(spec: ToolSpec, system: System) -> tuple[Status, str, str]:
    """Things worth knowing beyond "is it installed": (status, detail, hint)."""
    if spec.name == "docker" and not system.run(["docker", "info"], timeout=10).ok:
        return (
            "warn",
            "installed, but the daemon is not reachable",
            "sudo systemctl start docker    # and check you are in the docker group",
        )
    if spec.name == "gh" and not system.run(["gh", "auth", "status"], timeout=10).ok:
        return "warn", "installed, but not signed in", "gh auth login"
    return "ok", "", ""


def tools_section(config: Config, system: System) -> Section:
    section = Section("Tools")
    catalog = load_catalog(config.directory)
    for name in DOCTOR_TOOLS:
        spec = catalog.get(name)
        if spec is None:
            continue
        found = detect(spec, system)
        if not found.present:
            section.checks.append(
                Check(name, "warn", "not installed", f"gonk tools install {name}")
            )
            continue
        if not found.acceptable:
            section.checks.append(
                Check(
                    name,
                    "warn",
                    f"{found.version} (older than {spec.min_version})",
                    f"gonk tools install {name}",
                )
            )
            continue
        status, detail, hint = _tool_extras(spec, system)
        version = found.version or "installed"
        section.checks.append(
            Check(name, status, f"{version} — {detail}" if detail else version, hint)
        )
    return section


def gonk_section(config: Config, system: System, config_error: str) -> Section:
    section = Section("Gonk")
    add = section.checks.append
    add(Check("Version", "ok", __version__))

    executable = system.which("gonk")
    if executable:
        add(Check("Installed", "ok", executable))
    else:
        add(
            Check(
                "Installed",
                "warn",
                "'gonk' is not on PATH; running from a checkout?",
                "./lander/install.sh",
            )
        )

    if config_error:
        add(Check("Configuration", "fail", config_error, f"Fix {config.path}, then run again."))
    elif config.exists:
        add(Check("Configuration", "ok", str(config.path)))
    else:
        add(
            Check(
                "Configuration",
                "info",
                "using defaults; no config file yet",
                "gonk config init",
            )
        )
    for warning in config.warnings:
        add(Check("Configuration", "warn", warning, f"Check the spelling in {config.path}."))

    credentials = config.directory / "credentials.yaml"
    for path in (credentials, config.directory / "devices.json"):
        if path.exists() and hasattr(os, "getuid") and path.stat().st_mode & 0o077:
            add(
                Check(
                    path.name,
                    "fail",
                    "readable by other users",
                    f"chmod 600 {path}",
                )
            )

    try:
        profiles = load_profiles(config.directory)
        name = config.lander.default_profile
        wanted = resolve_tools(name, profiles)
        catalog = load_catalog(config.directory)
        present = sum(
            1 for tool in wanted if tool in catalog and detect(catalog[tool], system).present
        )
        if present == len(wanted):
            add(Check(f"Profile '{name}'", "ok", f"all {len(wanted)} tools present"))
        else:
            add(
                Check(
                    f"Profile '{name}'",
                    "warn",
                    f"{present} of {len(wanted)} tools present",
                    f"gonk land {name}",
                )
            )
    except GonkError as error:
        add(Check("Profiles", "fail", error.message))
    return section


def home_section(config: Config, system: System) -> Section:
    section = Section(f"Home ({config.home.name})")
    report = home_status.inspect(config, system)
    section.checks.extend(report.checks)
    if not report.is_here:
        section.checks.insert(
            0, Check("Transport", "info", f"{config.home.transport} → {config.home.host}")
        )
    return section


def agent_section(config: Config, system: System) -> Section:
    section = Section("Agent")
    add = section.checks.append
    is_home = system.hostname().lower() == config.home.name.lower()
    state = service.state(system)

    if state == "not-installed" and not is_home:
        add(Check("Service", "info", "not installed (only needed on machines you own)"))
        return section

    if state == "active":
        add(Check("Service", "ok", "running"))
    elif state == "not-installed":
        add(Check("Service", "warn", "not installed", "gonk agent install && gonk agent start"))
    else:
        add(Check("Service", "fail", state, "gonk agent start    # then: gonk agent status"))

    try:
        devices = DeviceStore(config.directory).devices()
    except GonkError as error:
        add(Check("Devices", "fail", error.message))
        return section
    if devices:
        add(Check("Devices", "ok", ", ".join(device.name for device in devices)))
    else:
        add(Check("Devices", "warn", "none", "gonk agent token create <device-name>"))
    add(Check("Listens on", "info", f"{config.agent.bind}:{config.agent.port}"))
    if config.agent.allow_public_bind:
        add(
            Check(
                "Public bind",
                "warn",
                "agent.allow_public_bind is on",
                "Read docs/security.md; the agent speaks plain HTTP.",
            )
        )
    return section


def mcp_section(config: Config, system: System) -> Section:
    section = Section("MCP")
    add = section.checks.append
    if not config.mcp.enabled:
        add(Check("Server", "info", "switched off in config"))
        return section

    registry = build_registry(config.directory)
    policy = Policy.from_config(config)
    capabilities = registry.all()
    allowed = [item for item in capabilities if policy.check(item).allowed]
    add(Check("Tools", "ok", f"{len(allowed)} of {len(capabilities)} allowed by policy"))

    risky = [item.name for item in allowed if item.risk == "dangerous"]
    if risky:
        add(Check("Dangerous tools enabled", "warn", ", ".join(risky)))

    plugins = sorted({item.source for item in capabilities if item.source != "built-in"})
    if plugins:
        add(Check("Plugins", "ok", ", ".join(plugins)))
    for problem in registry.problems:
        add(Check("Plugin", "fail", problem, f"Look in {config.directory / 'plugins'}"))

    token = "yes" if home_token(config, system.env) else "no"
    add(Check("Can serve tools from home", "info", f"device token present: {token}"))
    return section


def examine(config: Config, system: System, config_error: str = "") -> Report:
    return Report(
        [
            system_section(system),
            tools_section(config, system),
            gonk_section(config, system, config_error),
            home_section(config, system),
            agent_section(config, system),
            mcp_section(config, system),
        ]
    )
