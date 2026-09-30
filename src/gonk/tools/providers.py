"""Providers turn a catalog entry into commands.

This is the only module that knows how a package manager is invoked.
A provider never runs anything; it describes what would be run.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from gonk.core.errors import GonkError
from gonk.core.platform import Platform
from gonk.core.system import System

PACKAGE_NAME = re.compile(r"^[A-Za-z0-9@][A-Za-z0-9@/._+-]*$")
SCRIPT_URL = re.compile(r"^https://[A-Za-z0-9.-]+(/[A-Za-z0-9._~/%+-]*)?$")
SCRIPT_SHELLS = ("sh", "bash")

# $1 is the URL, $2 the shell. The script is fetched completely before any of
# it runs, so a dropped connection cannot execute half an installer.
FETCH_AND_RUN = 'set -e; body="$(curl -fsSL "$1")"; printf "%s\\n" "$body" | "$2" -s'


@dataclass(frozen=True)
class Command:
    argv: tuple[str, ...]
    needs_root: bool = False
    env: tuple[tuple[str, str], ...] = ()
    label: str = ""  # shown instead of argv when argv is not pleasant to read

    def full_argv(self, as_root: bool) -> list[str]:
        """The argv to execute, with sudo in front when it is needed."""
        if self.needs_root and not as_root:
            assignments = [f"{key}={value}" for key, value in self.env]
            return ["sudo", *(["env", *assignments] if assignments else []), *self.argv]
        return list(self.argv)

    def display(self, as_root: bool = False) -> str:
        if self.label:
            return self.label
        return shlex.join(self.full_argv(as_root))


def _packages(options: Mapping[str, Any], key: str = "packages") -> list[str]:
    value = options.get(key)
    names = [value] if isinstance(value, str) else value
    if not isinstance(names, list) or not names:
        raise GonkError(f"Provider options need '{key}'.")
    for name in names:
        if not isinstance(name, str) or not PACKAGE_NAME.match(name):
            raise GonkError(f"'{name}' does not look like a package name.")
    return list(names)


class Provider:
    name = ""
    executable = ""
    platforms: tuple[str, ...] = ("linux",)

    def available(self, platform: Platform, system: System, planned: frozenset[str]) -> bool:
        """Can this provider be used here? `planned` holds commands that earlier
        steps of the same plan will have installed by the time this one runs."""
        if platform.os not in self.platforms:
            return False
        return self.executable in planned or system.which(self.executable) is not None

    def refresh(self) -> Command | None:
        """Run once per session before the first install, e.g. `apt-get update`."""
        return None

    def commands(self, options: Mapping[str, Any], system: System) -> list[Command]:
        raise NotImplementedError


class Apt(Provider):
    name = "apt"
    executable = "apt-get"
    env = (("DEBIAN_FRONTEND", "noninteractive"),)

    def refresh(self) -> Command:
        return Command(("apt-get", "update", "-qq"), needs_root=True, env=self.env)

    def commands(self, options: Mapping[str, Any], system: System) -> list[Command]:
        argv = ("apt-get", "install", "-y", "--no-install-recommends", *_packages(options))
        return [Command(argv, needs_root=True, env=self.env)]


class Dnf(Provider):
    name = "dnf"
    executable = "dnf"

    def commands(self, options: Mapping[str, Any], system: System) -> list[Command]:
        return [Command(("dnf", "install", "-y", *_packages(options)), needs_root=True)]


class Pacman(Provider):
    name = "pacman"
    executable = "pacman"

    def commands(self, options: Mapping[str, Any], system: System) -> list[Command]:
        argv = ("pacman", "-S", "--needed", "--noconfirm", *_packages(options))
        return [Command(argv, needs_root=True)]


class Brew(Provider):
    name = "brew"
    executable = "brew"
    platforms = ("macos", "linux")

    def commands(self, options: Mapping[str, Any], system: System) -> list[Command]:
        cask = ["--cask"] if options.get("cask") else []
        return [Command(("brew", "install", *cask, *_packages(options)))]


class Winget(Provider):
    name = "winget"
    executable = "winget"
    platforms = ("windows",)

    def commands(self, options: Mapping[str, Any], system: System) -> list[Command]:
        (identifier,) = _packages(options, "id")[:1]
        argv = (
            "winget", "install", "--exact", "--id", identifier,
            "--accept-package-agreements", "--accept-source-agreements",
        )  # fmt: skip
        return [Command(argv)]


class Npm(Provider):
    """Global npm packages, installed under ~/.local so that root is not needed."""

    name = "npm"
    executable = "npm"
    platforms = ("linux", "macos", "windows")

    def commands(self, options: Mapping[str, Any], system: System) -> list[Command]:
        (package,) = _packages(options, "package")[:1]
        prefix = str(system.home() / ".local")
        return [Command(("npm", "install", "--global", "--prefix", prefix, package))]


class Script(Provider):
    """An official installer script, fetched over HTTPS."""

    name = "script"
    executable = "curl"
    platforms = ("linux", "macos")

    def commands(self, options: Mapping[str, Any], system: System) -> list[Command]:
        url, shell = options.get("url", ""), options.get("shell", "sh")
        if not isinstance(url, str) or not SCRIPT_URL.match(url):
            raise GonkError(f"'{url}' is not a plain https:// URL, so Gonk will not run it.")
        if shell not in SCRIPT_SHELLS:
            raise GonkError(f"Scripts run with {' or '.join(SCRIPT_SHELLS)}, not '{shell}'.")
        env = options.get("env", {})
        if not isinstance(env, dict):
            raise GonkError("Script env should map names to values.")
        return [
            Command(
                ("sh", "-c", FETCH_AND_RUN, "gonk-script", url, shell),
                env=tuple((str(key), str(value)) for key, value in env.items()),
                label=f"curl -fsSL {url} | {shell}",
            )
        ]


PROVIDERS: dict[str, Provider] = {
    provider.name: provider
    for provider in (Apt(), Dnf(), Pacman(), Brew(), Winget(), Npm(), Script())
}
