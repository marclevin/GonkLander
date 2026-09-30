"""A machine that exists only in memory.

Every test that would run a command, look at PATH or open a socket does it
against FakeSystem. Nothing here can reach the real machine.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import NoReturn

from gonk.core.system import NOT_FOUND, Result, System


class Replaced(Exception):
    """Raised where the real System would have replaced the process."""

    def __init__(self, argv: list[str]) -> None:
        super().__init__(" ".join(argv))
        self.argv = argv


class FakeSystem(System):
    def __init__(
        self,
        *,
        installed: Mapping[str, str] | None = None,
        packages: Mapping[str, Sequence[str]] | None = None,
        results: Mapping[str, Result] | None = None,
        files: Mapping[str, str] | None = None,
        env: Mapping[str, str] | None = None,
        home: Path = Path("/home/tester"),
        hostname: str = "borrowed",
        root: bool = False,
        resolvable: Sequence[str] = (),
        open_ports: Sequence[tuple[str, int]] = (),
        exit_code: int = 0,
    ) -> None:
        super().__init__(env={"PATH": "/usr/bin", **(env or {})})
        # command name -> what it prints when asked for its version
        self.installed = dict(installed or {})
        # a word in an install command -> the commands that installing it provides
        self.packages = {name: list(commands) for name, commands in (packages or {}).items()}
        # "git status" -> Result, matched against the start of the command line
        self.results = dict(results or {})
        self.files = dict(files or {})
        self._home = home
        self._hostname = hostname
        self._root = root
        self.resolvable = set(resolvable)
        self.open_ports = set(open_ports)
        self.exit_code = exit_code

        self.ran: list[list[str]] = []  # captured commands (read-only by convention)
        self.changed: list[list[str]] = []  # interactive commands: the ones that alter things

    def home(self) -> Path:
        return self._home

    def hostname(self) -> str:
        return self._hostname

    def is_root(self) -> bool:
        return self._root

    def which(self, command: str) -> str | None:
        return f"/usr/bin/{command}" if command in self.installed else None

    def read_text(self, path: str) -> str | None:
        return self.files.get(path)

    def disk_usage(self, path: str) -> tuple[int, int] | None:
        return (100 * 1024**3, 40 * 1024**3)

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = 30,
        env: Mapping[str, str] | None = None,
    ) -> Result:
        self.ran.append(list(argv))
        line = " ".join(argv)
        matches = [key for key in self.results if line.startswith(key)]
        if matches:
            return self.results[max(matches, key=len)]
        if argv[0] not in self.installed:
            return Result(NOT_FOUND, "", f"{argv[0]}: command not found")
        return Result(0, self.installed[argv[0]])

    def run_interactive(self, argv: Sequence[str], *, env: Mapping[str, str] | None = None) -> int:
        self.changed.append(list(argv))
        if self.exit_code == 0:
            for word in argv:
                for command in self.packages.get(word, ()):
                    self.installed.setdefault(command, f"{command} 9.9.9")
        return self.exit_code

    def replace_process(self, argv: Sequence[str]) -> NoReturn:
        raise Replaced(list(argv))

    def resolves(self, host: str) -> bool:
        return host in self.resolvable

    def tcp_probe(self, host: str, port: int, timeout: float = 3) -> str:
        return "" if (host, port) in self.open_ports else "connection refused"


UBUNTU = """\
PRETTY_NAME="Ubuntu 24.04.1 LTS"
NAME="Ubuntu"
VERSION_ID="24.04"
ID=ubuntu
ID_LIKE=debian
"""

FEDORA = """\
NAME="Fedora Linux"
VERSION_ID=41
ID=fedora
PRETTY_NAME="Fedora Linux 41 (Workstation Edition)"
"""
