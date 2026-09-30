"""The one door between Gonk and the machine it runs on.

Everything that runs a command, looks something up on PATH or opens a socket
goes through `System`. Tests replace it with a fake, which is why no test can
install a package or touch the real home directory.
"""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NoReturn

# Places tools land that are often missing from PATH right after an install.
EXTRA_BIN_DIRS = (".local/bin", ".cargo/bin", ".local/share/pnpm")

NOT_FOUND = 127
TIMED_OUT = 124


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str = ""
    stderr: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def text(self) -> str:
        """stdout, or stderr for the tools that print their version there."""
        return (self.stdout or self.stderr).strip()


class System:
    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self.env: Mapping[str, str] = os.environ if env is None else env

    # --- facts -----------------------------------------------------------

    def home(self) -> Path:
        return Path.home()

    def hostname(self) -> str:
        return socket.gethostname().split(".")[0]

    def is_root(self) -> bool:
        return hasattr(os, "geteuid") and os.geteuid() == 0

    def search_path(self) -> str:
        extra = [str(self.home() / directory) for directory in EXTRA_BIN_DIRS]
        return os.pathsep.join([self.env.get("PATH", ""), *extra])

    def which(self, command: str) -> str | None:
        return shutil.which(command, path=self.search_path())

    def read_text(self, path: str) -> str | None:
        try:
            return Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def disk_usage(self, path: str) -> tuple[int, int] | None:
        """(total, free) in bytes."""
        try:
            usage = shutil.disk_usage(path)
        except OSError:
            return None
        return usage.total, usage.free

    # --- commands --------------------------------------------------------

    def _environment(self, extra: Mapping[str, str] | None) -> dict[str, str]:
        environment = dict(self.env)
        environment["PATH"] = self.search_path()
        if extra:
            environment.update(extra)
        return environment

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float = 30,
        env: Mapping[str, str] | None = None,
    ) -> Result:
        """Run a command and capture its output. Never raises."""
        executable = self.which(argv[0])
        if executable is None:
            return Result(NOT_FOUND, "", f"{argv[0]}: command not found")
        try:
            done = subprocess.run(
                [executable, *argv[1:]],
                capture_output=True,
                text=True,
                errors="replace",
                timeout=timeout,
                env=self._environment(env),
                stdin=subprocess.DEVNULL,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return Result(TIMED_OUT, "", f"{argv[0]}: timed out after {timeout:g}s")
        except OSError as error:
            return Result(NOT_FOUND, "", f"{argv[0]}: {error.strerror or error}")
        return Result(done.returncode, done.stdout, done.stderr)

    def run_interactive(self, argv: Sequence[str], *, env: Mapping[str, str] | None = None) -> int:
        """Run a command attached to the terminal, so prompts and progress show."""
        executable = self.which(argv[0])
        if executable is None:
            return NOT_FOUND
        try:
            return subprocess.run(
                [executable, *argv[1:]], env=self._environment(env), check=False
            ).returncode
        except OSError:
            return NOT_FOUND

    def replace_process(self, argv: Sequence[str]) -> NoReturn:
        """Become another program. Used for ssh, so signals and the tty just work."""
        executable = self.which(argv[0])
        if executable is None:
            raise FileNotFoundError(argv[0])
        os.execv(executable, list(argv))  # noqa: S606 - argv list, no shell

    # --- network ---------------------------------------------------------

    def resolves(self, host: str) -> bool:
        try:
            socket.getaddrinfo(host, None)
        except OSError:
            return False
        return True

    def tcp_probe(self, host: str, port: int, timeout: float = 3) -> str:
        """Try to connect. Returns "" on success, or a short reason."""
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return ""
        except TimeoutError:
            return "timed out"
        except ConnectionRefusedError:
            return "connection refused"
        except socket.gaierror:
            return "name does not resolve"
        except OSError as error:
            return error.strerror or str(error)
