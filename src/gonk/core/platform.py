"""Work out what kind of machine this is."""

from __future__ import annotations

import platform as stdlib_platform
from dataclasses import dataclass

from gonk.core.system import System

ARCH_ALIASES = {
    "x86_64": "x86_64",
    "amd64": "x86_64",
    "aarch64": "arm64",
    "arm64": "arm64",
    "armv7l": "armv7",
    "i386": "x86",
    "i686": "x86",
}

OS_ALIASES = {"linux": "linux", "darwin": "macos", "windows": "windows"}

# Package managers in the order we would rather use them.
PACKAGE_MANAGERS = {
    "linux": ("apt-get", "dnf", "pacman", "brew"),
    "macos": ("brew",),
    "windows": ("winget",),
}


@dataclass(frozen=True)
class Platform:
    os: str  # linux | macos | windows | unknown
    arch: str
    distro: str = ""
    distro_like: tuple[str, ...] = ()
    version: str = ""
    pretty_name: str = ""
    is_wsl: bool = False

    @property
    def supported(self) -> bool:
        return self.os in OS_ALIASES.values()


def normalize_arch(machine: str) -> str:
    return ARCH_ALIASES.get(machine.lower(), machine.lower() or "unknown")


def normalize_os(name: str) -> str:
    return OS_ALIASES.get(name.lower(), "unknown")


def parse_os_release(text: str) -> dict[str, str]:
    """Parse /etc/os-release into a dict. Quotes are stripped, junk is ignored."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def describe(
    os_name: str,
    machine: str,
    os_release: str | None = None,
    kernel_version: str | None = None,
    os_version: str = "",
) -> Platform:
    """Build a Platform from raw facts. Pure, so it can be tested with fixtures."""
    system = normalize_os(os_name)
    arch = normalize_arch(machine)
    if system != "linux":
        pretty = {"macos": "macOS", "windows": "Windows"}.get(system, os_name or "unknown")
        return Platform(
            os=system, arch=arch, version=os_version, pretty_name=f"{pretty} {os_version}".strip()
        )

    release = parse_os_release(os_release or "")
    return Platform(
        os="linux",
        arch=arch,
        distro=release.get("ID", "").lower(),
        distro_like=tuple(release.get("ID_LIKE", "").lower().split()),
        version=release.get("VERSION_ID", ""),
        pretty_name=release.get("PRETTY_NAME", "Linux"),
        is_wsl="microsoft" in (kernel_version or "").lower(),
    )


def detect(system: System) -> Platform:
    os_name = stdlib_platform.system()
    os_version = ""
    if os_name == "Darwin":
        os_version = stdlib_platform.mac_ver()[0]
    elif os_name == "Windows":
        os_version = stdlib_platform.release()
    return describe(
        os_name,
        stdlib_platform.machine(),
        os_release=system.read_text("/etc/os-release"),
        kernel_version=system.read_text("/proc/version"),
        os_version=os_version,
    )


def package_manager(platform: Platform, system: System) -> str:
    """The first usable package manager, or ""."""
    for candidate in PACKAGE_MANAGERS.get(platform.os, ()):
        if system.which(candidate):
            return candidate
    return ""
