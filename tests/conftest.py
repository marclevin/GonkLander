from __future__ import annotations

from pathlib import Path

import pytest

from gonk.core.config import Config
from gonk.core.platform import Platform

from .fakes import FakeSystem


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Belt and braces: even code that reads the real environment lands in tmp_path."""
    monkeypatch.setenv("GONK_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("GONK_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    for name in ("GONK_HOME_TOKEN", "GONK_HOME_HOST", "GONK_HOME_NAME", "GONK_DEBUG"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def ubuntu() -> Platform:
    return Platform(
        os="linux",
        arch="x86_64",
        distro="ubuntu",
        distro_like=("debian",),
        version="24.04",
        pretty_name="Ubuntu 24.04.1 LTS",
    )


@pytest.fixture
def config(tmp_path: Path) -> Config:
    made = Config()
    made.directory = tmp_path / "config"
    made.state_directory = tmp_path / "state"
    return made


@pytest.fixture
def system(tmp_path: Path) -> FakeSystem:
    return FakeSystem(home=tmp_path / "home")
