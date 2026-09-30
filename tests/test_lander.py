"""The bootstrap scripts, run against a stand-in for uv inside a temporary home.

Nothing here downloads anything or installs anything real.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LANDER = ROOT / "lander"
INSTALL = LANDER / "install.sh"
PUBLIC_FILES = [
    INSTALL,
    LANDER / "install.ps1",
    LANDER / "shim" / "gonk",
    LANDER / "shim" / "gonk.ps1",
]

# Stands in for uv: records how it was called and "installs" a gonk that
# can report its version.
FAKE_UV = """#!/bin/sh
echo "$@" >> "$HOME/uv-calls.log"
case "$1" in
    --version) echo "uv 0.0.0-fake" ;;
    tool)
        if [ "${FAKE_UV_FAILS:-}" = "1" ]; then echo "error: no such version" >&2; exit 2; fi
        mkdir -p "$HOME/.local/bin"
        {
            echo '#!/bin/sh'
            echo 'echo "gonk 0.0.0-fake $*" >> "$HOME/gonk-calls.log"'
            echo 'echo "gonk 0.0.0-fake"'
        } > "$HOME/.local/bin/gonk"
        chmod +x "$HOME/.local/bin/gonk"
        ;;
esac
"""

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


@pytest.fixture
def home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    (home / ".local" / "bin").mkdir(parents=True)
    uv = home / ".local" / "bin" / "uv"
    uv.write_text(FAKE_UV)
    uv.chmod(0o755)
    return home


def install(home: Path, script: Path = INSTALL, **env: str) -> subprocess.CompletedProcess[str]:
    environment = {"HOME": str(home), "PATH": "/usr/bin:/bin", "TERM": "dumb", **env}
    return subprocess.run(
        ["bash", str(script)],
        env=environment,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        check=False,
    )


def uv_calls(home: Path) -> list[str]:
    return (home / "uv-calls.log").read_text().splitlines()


@pytest.mark.parametrize("path", PUBLIC_FILES, ids=lambda path: path.name)
def test_public_scripts_contain_no_secrets(path: Path) -> None:
    text = path.read_text()
    assert not re.search(r"gonk_[A-Za-z0-9_-]{20,}", text), "a device token"
    assert not re.search(r"(?i)(password|passwd|secret|api[_-]?key)\s*[:=]\s*\S", text)
    assert not re.search(r"-----BEGIN [A-Z ]*PRIVATE KEY-----", text)
    assert not re.search(r"\b(ghp|gho|github_pat|sk-ant|sk|tskey)[-_][A-Za-z0-9_-]{16,}", text)


@pytest.mark.parametrize("path", PUBLIC_FILES, ids=lambda path: path.name)
def test_the_bootstrap_never_asks_for_root(path: Path) -> None:
    """It may suggest a sudo command in a hint; it never runs one."""
    for line in path.read_text().splitlines():
        code = line.split("#")[0].strip()
        assert not re.match(r"(sudo|su|doas|pkexec)\b", code), line
        assert not re.search(r"[|;&(]\s*(sudo|doas|pkexec)\b", code), line


@pytest.mark.parametrize("path", [INSTALL, LANDER / "shim" / "gonk"], ids=lambda path: path.name)
def test_shell_scripts_parse(path: Path) -> None:
    subprocess.run(["bash", "-n", str(path)], check=True)


def test_a_truncated_download_runs_nothing(tmp_path: Path, home: Path) -> None:
    """The script does its work in a function called on the very last line."""
    lines = INSTALL.read_text().rstrip().splitlines()
    assert lines[-1] == 'main "$@"'

    truncated = tmp_path / "truncated.sh"
    truncated.write_text("\n".join(lines[: len(lines) * 2 // 3]) + "\n")
    install(home, truncated)
    assert not (home / "uv-calls.log").exists()


def test_installs_from_the_checkout_it_sits_in(home: Path) -> None:
    result = install(home)
    assert result.returncode == 0, result.stderr
    assert "Gonk has landed." in result.stdout
    assert "linux" in result.stdout or "macos" in result.stdout
    tool_calls = [call for call in uv_calls(home) if call.startswith("tool install")]
    assert tool_calls == [f"tool install --force --reinstall --quiet {ROOT}"]


def test_running_it_again_repairs_rather_than_breaks(home: Path) -> None:
    first, second = install(home), install(home)
    assert (first.returncode, second.returncode) == (0, 0)
    assert "Gonk has landed." in second.stdout
    calls = [call for call in uv_calls(home) if call.startswith("tool install")]
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert "--force" in calls[1]


def test_it_never_edits_shell_profiles(home: Path) -> None:
    for name in (".bashrc", ".profile", ".zshrc"):
        (home / name).write_text("# mine\n")
    result = install(home)
    assert result.returncode == 0
    for name in (".bashrc", ".profile", ".zshrc"):
        assert (home / name).read_text() == "# mine\n"
    assert "is not on your PATH" in result.stdout  # it tells you instead
    assert 'export PATH="$HOME/.local/bin:$PATH"' in result.stdout


def test_path_already_set_up(home: Path) -> None:
    result = install(home, PATH=f"{home}/.local/bin:/usr/bin:/bin")
    assert "is on your PATH" in result.stdout
    assert "export PATH" not in result.stdout


def test_version_and_repository_can_be_chosen(home: Path, tmp_path: Path) -> None:
    # Copied elsewhere, the script is no longer inside a checkout, as when piped from curl.
    lone = tmp_path / "elsewhere" / "install.sh"
    lone.parent.mkdir()
    shutil.copy(INSTALL, lone)

    install(home, lone)
    install(home, lone, GONK_VERSION="v0.1.0")
    install(home, lone, GONK_VERSION="dev", GONK_REPO="someone/Fork")
    install(home, lone, GONK_SOURCE="/opt/gonk")
    sources = [call.split("--quiet ")[1] for call in uv_calls(home) if "tool install" in call]
    base = "gonklander @ https://github.com"
    assert sources == [
        f"{base}/marclevin/GonkLander/archive/refs/heads/main.tar.gz",
        f"{base}/marclevin/GonkLander/archive/refs/tags/v0.1.0.tar.gz",
        f"{base}/someone/Fork/archive/refs/heads/dev.tar.gz",
        "/opt/gonk",
    ]


def test_a_profile_can_be_landed_straight_away(home: Path) -> None:
    result = install(home, GONK_PROFILE="dev")
    assert result.returncode == 0, result.stderr
    calls = (home / "gonk-calls.log").read_text()
    assert "land dev" in calls


def test_failure_is_explained(home: Path) -> None:
    result = install(home, FAKE_UV_FAILS="1", GONK_VERSION="v9.9.9")
    assert result.returncode == 1
    assert "gonk could not be installed" in result.stderr
    assert "Try:" in result.stderr
    assert "GONK_VERSION=v9.9.9" in result.stderr
    assert "Gonk has landed." not in result.stdout


def test_an_unwritable_home_is_explained(tmp_path: Path) -> None:
    if os.geteuid() == 0:
        pytest.skip("root can write anywhere")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    try:
        result = install(locked)
    finally:
        locked.chmod(0o700)
    assert result.returncode == 1
    assert "is not writable" in result.stderr


def test_the_shim_is_tiny_and_points_at_the_repository() -> None:
    text = (LANDER / "shim" / "gonk").read_text()
    assert "raw.githubusercontent.com/${repo}/${version}/lander/install.sh" in text
    assert len(text.splitlines()) < 50
