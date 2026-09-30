from __future__ import annotations

import stat
from importlib import resources
from pathlib import Path

import pytest
import yaml

from gonk.core import config as configuration
from gonk.core import paths
from gonk.core.config import Config
from gonk.core.errors import GonkError

from .fakes import FakeSystem


def write(directory: Path, text: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.yaml").write_text(text)


def load(tmp_path: Path, env: dict[str, str] | None = None) -> Config:
    env = {"GONK_CONFIG_DIR": str(tmp_path / "config"), **(env or {})}
    return configuration.load(FakeSystem(env=env, home=tmp_path / "home"))


def test_defaults_without_a_file(tmp_path: Path) -> None:
    config = load(tmp_path)
    assert not config.exists
    assert config.home.name == "gonksystem"
    assert config.home.transport == "tailscale"
    assert config.lander.default_profile == "dev"
    assert config.agent.bind == "127.0.0.1"
    assert config.agent.allow_public_bind is False
    assert config.policy.allow == []


def test_file_overrides_defaults(tmp_path: Path) -> None:
    write(tmp_path / "config", "home:\n  host: 100.64.0.7\n  ssh_port: 2222\n")
    config = load(tmp_path)
    assert config.exists
    assert config.home.host == "100.64.0.7"
    assert config.home.ssh_port == 2222
    assert config.home.name == "gonksystem"  # untouched settings keep their defaults


def test_environment_overrides_file(tmp_path: Path) -> None:
    write(tmp_path / "config", "home:\n  host: from-file\n")
    config = load(tmp_path, {"GONK_HOME_HOST": "from-env", "GONK_HOME_SSH_PORT": "2200"})
    assert config.home.host == "from-env"
    assert config.home.ssh_port == 2200


def test_environment_lists_and_booleans(tmp_path: Path) -> None:
    config = load(tmp_path, {"GONK_POLICY_ALLOW": "files.read, mphil.*", "GONK_MCP_ENABLED": "no"})
    assert config.policy.allow == ["files.read", "mphil.*"]
    assert config.mcp.enabled is False


def test_a_wrong_type_is_explained(tmp_path: Path) -> None:
    write(tmp_path / "config", "home:\n  ssh_port: twenty-two\n")
    with pytest.raises(GonkError) as caught:
        load(tmp_path)
    assert "home.ssh_port should be a whole number" in caught.value.message
    assert "config.yaml" in caught.value.message


def test_a_security_setting_is_never_guessed(tmp_path: Path) -> None:
    write(tmp_path / "config", "agent:\n  allow_public_bind: maybe\n")
    with pytest.raises(GonkError, match="true or false"):
        load(tmp_path)


def test_unknown_keys_are_warnings_not_errors(tmp_path: Path) -> None:
    write(tmp_path / "config", "home:\n  hots: typo\nnonsense:\n  a: 1\n")
    config = load(tmp_path)
    assert "unknown setting 'home.hots' is ignored" in config.warnings
    assert "unknown section 'nonsense' is ignored" in config.warnings


def test_invalid_yaml_names_the_line(tmp_path: Path) -> None:
    write(tmp_path / "config", "home:\n  host: [unclosed\n")
    with pytest.raises(GonkError, match="not valid YAML"):
        load(tmp_path)


def test_commands_must_be_lists_of_words(tmp_path: Path) -> None:
    write(tmp_path / "config", "commands:\n  disk: [df, -h]\n")
    assert load(tmp_path).commands == {"disk": ["df", "-h"]}

    write(tmp_path / "config", "commands:\n  disk: df -h\n")
    with pytest.raises(GonkError, match="list of words"):
        load(tmp_path)


def test_the_example_file_is_valid_and_matches_the_defaults() -> None:
    text = resources.files("gonk.data").joinpath("config.example.yaml").read_text()
    config = configuration.build(yaml.safe_load(text), {})
    assert config.warnings == []
    assert config.settings() == Config().settings()


def test_set_value_round_trips(tmp_path: Path) -> None:
    directory = tmp_path / "config"
    assert configuration.set_value(directory, "home.ssh_port", "2222") == 2222
    assert configuration.set_value(directory, "policy.allow", "files.read,mphil.*") == [
        "files.read",
        "mphil.*",
    ]
    config = load(tmp_path)
    assert config.home.ssh_port == 2222
    assert config.policy.allow == ["files.read", "mphil.*"]


def test_set_value_rejects_unknown_settings(tmp_path: Path) -> None:
    with pytest.raises(GonkError, match="not a setting"):
        configuration.set_value(tmp_path, "home.hots", "x")
    with pytest.raises(GonkError, match="not a setting"):
        configuration.set_value(tmp_path, "nonsense", "x")


def test_token_is_stored_privately(tmp_path: Path, config: Config) -> None:
    path = configuration.save_home_token(config, "gonk_secret")
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert configuration.home_token(config, {}) == "gonk_secret"


def test_token_from_environment_wins(config: Config) -> None:
    configuration.save_home_token(config, "gonk_on_disk")
    assert configuration.home_token(config, {"GONK_HOME_TOKEN": "gonk_env"}) == "gonk_env"


def test_no_token_is_an_empty_string(config: Config) -> None:
    assert configuration.home_token(config, {}) == ""


@pytest.mark.parametrize(
    ("key", "env", "expected"),
    [
        ("linux", {}, "/h/.config/gonk"),
        ("linux", {"XDG_CONFIG_HOME": "/x"}, "/x/gonk"),
        ("macos", {}, "/h/Library/Application Support/gonk"),
        ("linux", {"GONK_CONFIG_DIR": "/custom"}, "/custom"),
    ],
)
def test_config_directory(key: str, env: dict[str, str], expected: str) -> None:
    assert paths.config_dir(env, Path("/h"), key) == Path(expected)


def test_windows_directories() -> None:
    env = {"APPDATA": "C:/Users/m/AppData/Roaming", "LOCALAPPDATA": "C:/Users/m/AppData/Local"}
    assert paths.config_dir(env, Path("/h"), "windows").name == "gonk"
    assert "Roaming" in str(paths.config_dir(env, Path("/h"), "windows"))
    assert "Local" in str(paths.state_dir(env, Path("/h"), "windows"))


def test_state_directory_follows_xdg() -> None:
    assert paths.state_dir({}, Path("/h"), "linux") == Path("/h/.local/state/gonk")
    assert paths.state_dir({"XDG_STATE_HOME": "/s"}, Path("/h"), "linux") == Path("/s/gonk")
