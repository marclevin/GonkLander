from __future__ import annotations

from pathlib import Path

from gonk import doctor
from gonk.core.config import Config, save_home_token
from gonk.core.system import Result

from .fakes import UBUNTU, FakeSystem


def box(tmp_path: Path, **installed: str) -> FakeSystem:
    return FakeSystem(
        installed={"apt-get": "", "git": "git version 2.43.0", **installed},
        files={"/etc/os-release": UBUNTU},
        env={"SHELL": "/bin/bash", "PATH": f"/usr/bin:{tmp_path}/home/.local/bin"},
        home=tmp_path / "home",
    )


def find(report: doctor.Report, section: str, name: str) -> doctor.Check:
    for candidate in report.sections:
        if candidate.title.startswith(section):
            for check in candidate.checks:
                if check.name == name:
                    return check
    raise AssertionError(f"no check {section}/{name}")


def test_the_report_covers_everything_asked_for(config: Config, tmp_path: Path) -> None:
    report = doctor.examine(config, box(tmp_path))
    titles = [section.title for section in report.sections]
    assert titles == ["System", "Tools", "Gonk", "Home (gonksystem)", "Agent", "MCP"]

    system_checks = {check.name for check in report.sections[0].checks}
    assert {"OS", "Architecture", "Shell", "Package manager"} <= system_checks
    tool_checks = {check.name for check in report.sections[1].checks}
    assert {"git", "python", "uv", "node", "npm", "pnpm", "docker", "gh", "ssh", "tailscale"} <= (
        tool_checks
    )


def test_missing_tools_are_warnings_with_a_fix(config: Config, tmp_path: Path) -> None:
    report = doctor.examine(config, box(tmp_path))
    node = find(report, "Tools", "node")
    assert (node.status, node.hint) == ("warn", "gonk tools install node")
    assert find(report, "Tools", "git").status == "ok"


def test_a_bare_machine_has_warnings_but_is_not_broken(config: Config, tmp_path: Path) -> None:
    config.home.transport = "ssh"
    system = box(tmp_path)
    system.open_ports = {("gonksystem", 22)}
    report = doctor.examine(config, system)
    assert report.count("warn") > 0
    assert report.healthy


def test_an_unreachable_home_is_a_problem(config: Config, tmp_path: Path) -> None:
    report = doctor.examine(config, box(tmp_path))
    assert find(report, "Home", "Tailscale installed").status == "fail"
    assert not report.healthy


def test_a_broken_config_file_is_reported_not_fatal(config: Config, tmp_path: Path) -> None:
    report = doctor.examine(config, box(tmp_path), "home.ssh_port should be a whole number")
    check = find(report, "Gonk", "Configuration")
    assert check.status == "fail"
    assert "whole number" in check.detail
    assert len(report.sections) == 6  # the rest of the report is still there


def test_config_warnings_are_shown(config: Config, tmp_path: Path) -> None:
    config.warnings = ["unknown setting 'home.hots' is ignored"]
    report = doctor.examine(config, box(tmp_path))
    assert any("home.hots" in check.detail for check in report.sections[2].checks)


def test_docker_installed_but_daemon_down(config: Config, tmp_path: Path) -> None:
    system = box(tmp_path, docker="Docker version 27.3.1")
    system.results["docker info"] = Result(1, "", "Cannot connect to the Docker daemon")
    docker = find(doctor.examine(config, system), "Tools", "docker")
    assert docker.status == "warn"
    assert "daemon is not reachable" in docker.detail


def test_gh_installed_but_signed_out(config: Config, tmp_path: Path) -> None:
    system = box(tmp_path, gh="gh version 2.60.0")
    system.results["gh auth status"] = Result(1, "", "not logged in")
    gh = find(doctor.examine(config, system), "Tools", "gh")
    assert (gh.status, gh.hint) == ("warn", "gh auth login")


def test_credentials_others_can_read_are_a_problem(config: Config, tmp_path: Path) -> None:
    path = save_home_token(config, "gonk_token")
    path.chmod(0o644)
    check = find(doctor.examine(config, box(tmp_path)), "Gonk", "credentials.yaml")
    assert check.status == "fail"
    assert check.hint == f"chmod 600 {path}"


def test_dangerous_tools_are_called_out(config: Config, tmp_path: Path) -> None:
    config.policy.allow = ["commands.run"]
    check = find(doctor.examine(config, box(tmp_path)), "MCP", "Dangerous tools enabled")
    assert (check.status, check.detail) == ("warn", "commands.run")


def test_local_bin_missing_from_path(config: Config, tmp_path: Path) -> None:
    system = box(tmp_path)
    system.env = {"PATH": "/usr/bin", "SHELL": "/bin/bash"}
    check = find(doctor.examine(config, system), "System", "~/.local/bin on PATH")
    assert check.status == "warn"
    assert "export PATH" in check.hint


def test_the_report_can_be_data(config: Config, tmp_path: Path) -> None:
    data = doctor.examine(config, box(tmp_path)).as_data()
    assert set(data) == {"healthy", "sections"}
    assert data["sections"][0]["checks"][0].keys() == {"name", "status", "detail", "hint"}


def test_the_doctor_only_looks(config: Config, tmp_path: Path) -> None:
    system = box(tmp_path)
    doctor.examine(config, system)
    assert system.changed == []
