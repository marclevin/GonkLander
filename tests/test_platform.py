from __future__ import annotations

import pytest

from gonk.core import platform
from gonk.core.platform import Platform

from .fakes import FEDORA, UBUNTU, FakeSystem


def test_os_release_is_parsed_with_and_without_quotes() -> None:
    values = platform.parse_os_release(UBUNTU + "\n# a comment\nnonsense line\n")
    assert values["ID"] == "ubuntu"
    assert values["PRETTY_NAME"] == "Ubuntu 24.04.1 LTS"
    assert "nonsense line" not in values


def test_ubuntu_is_recognised_as_debian_like() -> None:
    machine = platform.describe("Linux", "x86_64", os_release=UBUNTU)
    assert (machine.os, machine.distro, machine.version) == ("linux", "ubuntu", "24.04")
    assert machine.distro_like == ("debian",)
    assert machine.supported


def test_fedora() -> None:
    machine = platform.describe("Linux", "aarch64", os_release=FEDORA)
    assert (machine.distro, machine.arch) == ("fedora", "arm64")


def test_linux_without_os_release_still_works() -> None:
    machine = platform.describe("Linux", "x86_64", os_release=None)
    assert machine.os == "linux"
    assert machine.distro == ""
    assert machine.pretty_name == "Linux"


def test_wsl_is_detected_from_the_kernel_version() -> None:
    kernel = "Linux version 5.15.153.1-microsoft-standard-WSL2"
    assert platform.describe("Linux", "x86_64", UBUNTU, kernel).is_wsl
    assert not platform.describe("Linux", "x86_64", UBUNTU, "Linux version 6.8.0-generic").is_wsl


@pytest.mark.parametrize(
    ("os_name", "expected"),
    [("Darwin", "macos"), ("Windows", "windows"), ("Linux", "linux"), ("Plan9", "unknown")],
)
def test_operating_systems(os_name: str, expected: str) -> None:
    assert platform.describe(os_name, "x86_64").os == expected


def test_unknown_os_is_not_supported() -> None:
    assert not platform.describe("Plan9", "x86_64").supported


@pytest.mark.parametrize(
    ("machine", "expected"),
    [
        ("x86_64", "x86_64"),
        ("AMD64", "x86_64"),
        ("aarch64", "arm64"),
        ("arm64", "arm64"),
        ("riscv64", "riscv64"),
        ("", "unknown"),
    ],
)
def test_architectures(machine: str, expected: str) -> None:
    assert platform.normalize_arch(machine) == expected


def test_package_manager_prefers_the_native_one(ubuntu: Platform) -> None:
    system = FakeSystem(installed={"brew": "", "apt-get": ""})
    assert platform.package_manager(ubuntu, system) == "apt-get"


def test_no_package_manager(ubuntu: Platform) -> None:
    assert platform.package_manager(ubuntu, FakeSystem()) == ""
