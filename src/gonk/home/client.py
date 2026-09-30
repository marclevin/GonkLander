"""Talk to the Gonk Agent at home."""

from __future__ import annotations

from typing import Any

import httpx

from gonk.core.errors import GonkError


class AgentUnreachable(GonkError):
    pass


class AgentRefused(GonkError):
    pass


class AgentClient:
    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        *,
        name: str = "home",
        timeout: float = 10,
        http: httpx.Client | None = None,
    ) -> None:
        self.name = name
        bracketed = f"[{host}]" if ":" in host and not host.startswith("[") else host
        self.base_url = f"http://{bracketed}:{port}"
        # `http` lets tests talk to an in-process agent instead of the network.
        self._client = http or httpx.Client(base_url=self.base_url, timeout=timeout)
        if token:
            self._client.headers["Authorization"] = f"Bearer {token}"

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.ConnectError:
            raise AgentUnreachable(
                f"Could not reach the Gonk Agent on {self.name} ({self.base_url}).",
                hints=[
                    "gonk home status     # shows which layer is failing",
                    f"On {self.name}: gonk agent status",
                ],
            ) from None
        except httpx.TimeoutException:
            raise AgentUnreachable(
                f"The Gonk Agent on {self.name} did not answer in time.",
                hints=["gonk home status"],
            ) from None
        except httpx.HTTPError as error:
            raise AgentUnreachable(f"Talking to the Gonk Agent failed: {error}") from None

        if response.status_code == 401:
            raise AgentRefused(
                f"The Gonk Agent on {self.name} did not accept this device's token.",
                hints=[
                    f"On {self.name}: gonk agent token create <this-device>",
                    "Here:       gonk home pair",
                ],
            )
        try:
            body = response.json()
        except ValueError:
            raise AgentUnreachable(
                f"Something answered on {self.base_url}, but it is not a Gonk Agent."
            ) from None
        if response.status_code >= 400:
            message = body.get("error", f"HTTP {response.status_code}")
            raise AgentRefused(str(message), hints=list(body.get("hints", [])))
        return body

    def whoami(self) -> dict[str, Any]:
        return dict(self._request("GET", "/v1/whoami"))

    def capabilities(self) -> list[dict[str, Any]]:
        return list(self._request("GET", "/v1/capabilities")["capabilities"])

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        body = self._request("POST", f"/v1/call/{name}", json={"arguments": arguments or {}})
        return body["result"]

    def close(self) -> None:
        self._client.close()
