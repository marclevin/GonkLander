"""Device tokens: who is allowed to talk to the agent."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from gonk.core.errors import GonkError
from gonk.core.paths import write_private

DEVICES_FILE = "devices.json"
PREFIX = "gonk_"
DEVICE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")


@dataclass(frozen=True)
class Device:
    name: str
    token_hash: str
    created: str


def _hash(token: str) -> str:
    # Tokens are 256 random bits, so a fast hash is enough: there is nothing to guess.
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class DeviceStore:
    """Tokens are shown once and stored only as hashes.

    The file is read on every check, so revoking a device takes effect on its
    very next request.
    """

    def __init__(self, directory: Path) -> None:
        self.path = directory / DEVICES_FILE

    def devices(self) -> list[Device]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, json.JSONDecodeError) as error:
            raise GonkError(
                f"Could not read {self.path}: {error}",
                hints=["If the file is damaged, delete it and create new tokens."],
            ) from error
        return [Device(**entry) for entry in data.get("devices", [])]

    def _save(self, devices: list[Device]) -> None:
        payload = {"devices": [vars(device) for device in devices]}
        write_private(self.path, json.dumps(payload, indent=2) + "\n")

    def create(self, name: str) -> str:
        """Register a device and return its token. The token is not kept."""
        if not DEVICE_NAME.match(name):
            raise GonkError(
                f"'{name}' is not a usable device name.",
                hints=["Use letters, digits, dots, dashes and underscores: work-laptop"],
            )
        devices = self.devices()
        if any(device.name == name for device in devices):
            raise GonkError(
                f"There is already a device called '{name}'.",
                hints=[f"gonk agent token revoke {name}    # then create it again"],
            )
        token = PREFIX + secrets.token_urlsafe(32)
        created = datetime.now(UTC).isoformat(timespec="seconds")
        self._save([*devices, Device(name, _hash(token), created)])
        return token

    def revoke(self, name: str) -> None:
        devices = self.devices()
        remaining = [device for device in devices if device.name != name]
        if len(remaining) == len(devices):
            known = ", ".join(device.name for device in devices) or "none"
            raise GonkError(f"There is no device called '{name}'. Known devices: {known}.")
        self._save(remaining)

    def verify(self, token: str) -> str | None:
        """The name of the device this token belongs to, or None."""
        if not token.startswith(PREFIX):
            return None
        candidate = _hash(token)
        found = None
        for device in self.devices():
            # Compare against every device, so timing does not depend on position.
            if hmac.compare_digest(candidate, device.token_hash):
                found = device.name
        return found
