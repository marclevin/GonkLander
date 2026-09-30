from __future__ import annotations

import json
import stat
from collections.abc import Iterator
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from gonk.agent.app import MAX_BODY_BYTES, create_app
from gonk.agent.bind import is_private_to_you, resolve_bind
from gonk.agent.service import unit_text
from gonk.agent.tokens import DeviceStore
from gonk.capabilities.invoke import Gate
from gonk.capabilities.loader import build_registry
from gonk.core.audit import AuditLog
from gonk.core.config import Config
from gonk.core.errors import GonkError
from gonk.core.system import Result
from gonk.policy import Policy

from .fakes import FakeSystem


@pytest.fixture
def devices(config: Config) -> DeviceStore:
    return DeviceStore(config.directory)


@pytest.fixture
def client(config: Config, devices: DeviceStore, tmp_path: Path) -> TestClient:
    system = FakeSystem(hostname="gonksystem")
    gate = Gate(build_registry(None), Policy(), AuditLog(tmp_path / "audit.log"), "agent")
    return TestClient(create_app(config, system, gate, devices))


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# --- tokens --------------------------------------------------------------------


def test_the_token_itself_is_never_stored(devices: DeviceStore) -> None:
    token = devices.create("laptop")
    assert token.startswith("gonk_")
    assert len(token) > 40
    stored = devices.path.read_text()
    assert token not in stored
    assert token.removeprefix("gonk_") not in stored
    assert stat.S_IMODE(devices.path.stat().st_mode) == 0o600


def test_tokens_are_verified_and_revoked(devices: DeviceStore) -> None:
    laptop, phone = devices.create("laptop"), devices.create("phone")
    assert devices.verify(laptop) == "laptop"
    assert devices.verify(phone) == "phone"
    assert devices.verify("gonk_made_up") is None
    assert devices.verify("") is None
    assert devices.verify(laptop.removeprefix("gonk_")) is None

    devices.revoke("laptop")
    assert devices.verify(laptop) is None
    assert devices.verify(phone) == "phone"  # other devices are unaffected


def test_every_token_is_different(devices: DeviceStore) -> None:
    assert devices.create("a") != devices.create("b")


def test_device_names(devices: DeviceStore) -> None:
    devices.create("work-laptop")
    with pytest.raises(GonkError, match="already a device"):
        devices.create("work-laptop")
    for name in ("", "has space", "../etc", "-flag", "x" * 100):
        with pytest.raises(GonkError, match="not a usable device name"):
            devices.create(name)
    with pytest.raises(GonkError, match="no device called 'ghost'"):
        devices.revoke("ghost")


# --- http ----------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [("GET", "/v1/whoami"), ("GET", "/v1/capabilities"), ("POST", "/v1/call/machine.status")],
)
def test_every_route_requires_a_token(client: TestClient, method: str, path: str) -> None:
    assert client.request(method, path).status_code == 401
    assert client.request(method, path, headers=bearer("gonk_wrong")).status_code == 401
    assert client.request(method, path, headers={"Authorization": "gonk_x"}).status_code == 401
    assert client.request(method, path, headers={"Authorization": "Basic abc"}).status_code == 401


def test_there_are_no_other_routes(client: TestClient, devices: DeviceStore) -> None:
    token = devices.create("laptop")
    for path in ("/", "/health", "/v1", "/v1/exec", "/v1/shell", "/docs"):
        assert client.get(path, headers=bearer(token)).status_code == 404


def test_whoami(client: TestClient, devices: DeviceStore) -> None:
    response = client.get("/v1/whoami", headers=bearer(devices.create("laptop")))
    assert response.status_code == 200
    assert response.json()["device"] == "laptop"
    assert response.json()["agent"] == "gonksystem"


def test_only_allowed_capabilities_are_offered(client: TestClient, devices: DeviceStore) -> None:
    response = client.get("/v1/capabilities", headers=bearer(devices.create("laptop")))
    offered = {item["name"]: item for item in response.json()["capabilities"]}
    assert "machine.status" in offered
    assert offered["machine.status"]["tool_name"] == "gonk_machine_status"
    assert "schema" in offered["machine.processes"]
    assert not {"files.read", "services.restart", "commands.run"} & set(offered)


def test_calling(client: TestClient, devices: DeviceStore) -> None:
    headers = bearer(devices.create("laptop"))
    ok = client.post("/v1/call/gonk.status", headers=headers)
    assert ok.status_code == 200
    assert ok.json()["result"]["hostname"] == "gonksystem"

    refused = client.post(
        "/v1/call/files.read", headers=headers, json={"arguments": {"path": "/x"}}
    )
    assert refused.status_code == 403

    unknown = client.post("/v1/call/shell.exec", headers=headers)
    assert unknown.status_code == 403
    assert unknown.json()["error"].replace("shell.exec", "X") == refused.json()["error"].replace(
        "files.read", "X"
    )

    wrong = client.post(
        "/v1/call/machine.processes", headers=headers, json={"arguments": {"limit": "1; reboot"}}
    )
    assert wrong.status_code == 422
    assert "whole number" in wrong.json()["error"]


def test_malformed_requests(client: TestClient, devices: DeviceStore) -> None:
    headers = bearer(devices.create("laptop"))
    path = "/v1/call/gonk.status"
    assert client.post(path, headers=headers, content=b"{not json").status_code == 400
    assert client.post(path, headers=headers, json={"arguments": [1, 2]}).status_code == 400
    assert client.post(path, headers=headers, json=["a", "list"]).status_code == 400
    huge = json.dumps({"arguments": {"x": "y" * MAX_BODY_BYTES}})
    assert client.post(path, headers=headers, content=huge).status_code == 413

    def drip() -> Iterator[bytes]:  # no Content-Length to give the size away
        for _ in range(MAX_BODY_BYTES // 1024 + 2):
            yield b"y" * 1024

    assert client.post(path, headers=headers, content=drip()).status_code == 413


def test_revoking_takes_effect_on_the_next_request(
    client: TestClient, devices: DeviceStore
) -> None:
    headers = bearer(devices.create("laptop"))
    assert client.get("/v1/whoami", headers=headers).status_code == 200
    devices.revoke("laptop")
    assert client.get("/v1/whoami", headers=headers).status_code == 401


def test_the_caller_is_recorded_by_device_name(
    client: TestClient, devices: DeviceStore, tmp_path: Path
) -> None:
    client.post("/v1/call/gonk.status", headers=bearer(devices.create("laptop")))
    client.get("/v1/whoami", headers=bearer("gonk_wrong"))
    events = AuditLog(tmp_path / "audit.log").tail()
    assert [(event["event"], event.get("caller")) for event in events] == [
        ("capability.call", "laptop"),
        ("agent.unauthorized", None),
    ]
    assert "gonk_wrong" not in (tmp_path / "audit.log").read_text()


# --- binding -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("100.101.102.103", True),  # tailnet
        ("fd7a:115c:a1e0::1", True),  # tailnet, IPv6
        ("0.0.0.0", False),  # noqa: S104
        ("::", False),
        ("192.168.1.10", False),  # a LAN is other people's machines too
        ("10.0.0.5", False),
        ("8.8.8.8", False),
        ("100.63.255.255", False),  # just outside the tailnet range
        ("gonksystem", False),
    ],
)
def test_which_addresses_are_private_to_you(address: str, expected: bool) -> None:
    assert is_private_to_you(address) is expected


def test_default_bind_is_loopback(config: Config, system: FakeSystem) -> None:
    assert resolve_bind(config, system) == "127.0.0.1"


@pytest.mark.parametrize("address", ["0.0.0.0", "::", "192.168.1.10", "8.8.8.8"])  # noqa: S104
def test_exposed_binds_are_refused(config: Config, system: FakeSystem, address: str) -> None:
    config.agent.bind = address
    with pytest.raises(GonkError, match="Refusing to listen"):
        resolve_bind(config, system)


def test_an_exposed_bind_needs_an_explicit_setting(config: Config, system: FakeSystem) -> None:
    config.agent.bind = "192.168.1.10"
    config.agent.allow_public_bind = True
    assert resolve_bind(config, system) == "192.168.1.10"


def test_bind_to_the_tailnet(config: Config) -> None:
    config.agent.bind = "tailscale"
    up = FakeSystem(
        installed={"tailscale": ""}, results={"tailscale ip": Result(0, "100.64.0.7\n")}
    )
    assert resolve_bind(config, up) == "100.64.0.7"
    with pytest.raises(GonkError, match="no tailnet address"):
        resolve_bind(config, FakeSystem())


def test_a_hostname_is_not_an_address(config: Config, system: FakeSystem) -> None:
    config.agent.bind = "example.com"
    with pytest.raises(GonkError, match="not an address"):
        resolve_bind(config, system)


def test_the_service_cannot_gain_privileges() -> None:
    unit = unit_text("/home/marc/.local/bin/gonk")
    assert "ExecStart=/home/marc/.local/bin/gonk agent run" in unit
    assert "NoNewPrivileges=yes" in unit
    assert "User=" not in unit  # a user service: it runs as whoever installed it
