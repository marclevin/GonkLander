from __future__ import annotations

from pathlib import Path

import pytest

from gonk.core.errors import GonkError
from gonk.tools.catalog import load_catalog
from gonk.tools.profiles import Profile, load_profiles, parse_profile, resolve_tools


def test_builtin_profiles_exist() -> None:
    assert {"minimal", "dev", "ai", "full"} <= set(load_profiles())


def test_every_tool_in_every_profile_is_in_the_catalog() -> None:
    catalog, profiles = load_catalog(), load_profiles()
    for name in profiles:
        missing = [tool for tool in resolve_tools(name, profiles) if tool not in catalog]
        assert not missing, f"profile {name} names unknown tools: {missing}"


def test_profiles_build_on_each_other() -> None:
    profiles = load_profiles()
    minimal, dev = resolve_tools("minimal", profiles), resolve_tools("dev", profiles)
    ai, full = resolve_tools("ai", profiles), resolve_tools("full", profiles)
    assert dev[: len(minimal)] == minimal  # inherited tools come first, in order
    assert set(dev) < set(ai) < set(full)
    assert "claude-code" in ai
    assert "claude-code" not in dev


def test_a_tool_is_listed_once() -> None:
    profiles = {
        "base": Profile("base", tools=("git", "jq")),
        "top": Profile("top", extends="base", tools=("jq", "tmux", "git")),
    }
    assert resolve_tools("top", profiles) == ["git", "jq", "tmux"]


def test_parse_profile() -> None:
    profile = parse_profile("name: x\ndescription: d\nextends: dev\ntools: [a, b]\n", "x.yaml")
    assert profile == Profile("x", "d", "dev", ("a", "b"))


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("tools: [a]", "needs a name"),
        ("name: x\ntools: a", "list of tool names"),
        ("name: x\ntoolz: [a]", "unknown setting"),
        ("- just\n- a list", "should contain"),
        ("name: [unclosed", "not valid YAML"),
    ],
)
def test_bad_profiles_are_explained(text: str, message: str) -> None:
    with pytest.raises(GonkError, match=message):
        parse_profile(text, "bad.yaml")


def test_unknown_profile_lists_the_real_ones() -> None:
    with pytest.raises(GonkError) as caught:
        resolve_tools("nope", load_profiles())
    assert "no profile called 'nope'" in caught.value.message
    assert "dev" in caught.value.hints[0]


def test_extending_a_missing_profile() -> None:
    with pytest.raises(GonkError, match="extends 'ghost', which does not exist"):
        resolve_tools("a", {"a": Profile("a", extends="ghost")})


def test_profiles_that_extend_each_other() -> None:
    profiles = {"a": Profile("a", extends="b"), "b": Profile("b", extends="a")}
    with pytest.raises(GonkError, match="loop: a → b → a"):
        resolve_tools("a", profiles)


def test_user_profiles_are_added_and_override(tmp_path: Path) -> None:
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "mphil.yaml").write_text("name: mphil\nextends: dev\ntools: [tree]\n")
    (tmp_path / "profiles" / "minimal.yaml").write_text("name: minimal\ntools: [git]\n")
    profiles = load_profiles(tmp_path)
    assert resolve_tools("minimal", profiles) == ["git"]
    assert resolve_tools("mphil", profiles)[0] == "git"
    assert "tree" in resolve_tools("mphil", profiles)
