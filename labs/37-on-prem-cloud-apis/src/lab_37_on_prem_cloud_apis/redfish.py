"""Bounded Redfish inventory client for data-centre management APIs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urljoin, urlparse

import httpx


class RedfishProtocolError(ValueError):
    """Raised when a Redfish response breaks the expected resource shape."""


@dataclass(frozen=True, slots=True)
class RedfishSystem:
    """Operational identity and health for one managed computer system."""

    system_id: str
    name: str
    model: str
    manufacturer: str
    serial_number: str
    power_state: str
    health: str


class RedfishClient:
    """Read Redfish service-root and computer-system inventory resources."""

    def __init__(
        self,
        base_url: str,
        client: httpx.Client,
        *,
        max_pages: int = 20,
        max_systems: int = 1_000,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("Redfish base URL must use HTTPS")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("Redfish credentials must not appear in the URL")
        if max_pages < 1 or max_systems < 1:
            raise ValueError("Redfish page and system limits must be positive")
        self._base_url = base_url.rstrip("/") + "/"
        self._origin = (parsed.scheme, parsed.netloc)
        self._client = client
        self._max_pages = max_pages
        self._max_systems = max_systems

    @classmethod
    def with_session_token(
        cls,
        base_url: str,
        token: str,
        *,
        verify: str | bool = True,
        timeout: float = 10.0,
    ) -> RedfishClient:
        """Create a client whose token and TLS policy stay in the transport."""

        if not token:
            raise ValueError("Redfish session token must not be empty")
        client = httpx.Client(
            headers={"X-Auth-Token": token},
            verify=verify,
            timeout=timeout,
        )
        return cls(base_url, client)

    def close(self) -> None:
        self._client.close()

    def service_root(self) -> dict[str, Any]:
        """Read and validate the Redfish service root."""

        document = self._get("/redfish/v1/")
        systems = document.get("Systems")
        if (
            not isinstance(systems, dict)
            or not isinstance(systems.get("@odata.id"), str)
            or not systems["@odata.id"]
        ):
            raise RedfishProtocolError("Redfish service root has no Systems collection")
        return document

    def list_systems(self) -> tuple[RedfishSystem, ...]:
        """Follow bounded same-origin collection pages and read every member."""

        root = self.service_root()
        systems_link = root["Systems"]
        assert isinstance(systems_link, dict)
        next_link: str | None = str(systems_link["@odata.id"])
        member_links: list[str] = []
        pages = 0
        while next_link is not None:
            pages += 1
            if pages > self._max_pages:
                raise RedfishProtocolError("Redfish Systems collection exceeds page limit")
            page = self._get(next_link)
            members = page.get("Members")
            if not isinstance(members, list):
                raise RedfishProtocolError("Redfish Systems page has no Members array")
            for member in members:
                if not isinstance(member, dict) or not isinstance(member.get("@odata.id"), str):
                    raise RedfishProtocolError("Redfish Systems member has no resource link")
                member_links.append(member["@odata.id"])
                if len(member_links) > self._max_systems:
                    raise RedfishProtocolError("Redfish Systems collection exceeds system limit")
            raw_next = page.get("Members@odata.nextLink")
            if raw_next is not None and not isinstance(raw_next, str):
                raise RedfishProtocolError("Redfish next link must be text")
            next_link = raw_next
        return tuple(self._parse_system(self._get(link)) for link in member_links)

    def get_system(self, system_id: str) -> RedfishSystem:
        """Read one system by an encoded path segment."""

        if not system_id or len(system_id) > 128:
            raise ValueError("Redfish system id must contain 1 to 128 characters")
        link = f"/redfish/v1/Systems/{quote(system_id, safe='')}"
        return self._parse_system(self._get(link))

    def _get(self, link: str) -> dict[str, Any]:
        url = urljoin(self._base_url, link)
        parsed = urlparse(url)
        if (parsed.scheme, parsed.netloc) != self._origin:
            raise RedfishProtocolError("Redfish resource link changed origin")
        response = self._client.get(url)
        response.raise_for_status()
        document = response.json()
        if not isinstance(document, dict):
            raise RedfishProtocolError("Redfish resource must be a JSON object")
        return document

    @staticmethod
    def _parse_system(document: dict[str, Any]) -> RedfishSystem:
        status = document.get("Status")
        if not isinstance(status, dict):
            raise RedfishProtocolError("Redfish ComputerSystem has no Status object")
        required = {
            "Id": document.get("Id"),
            "Name": document.get("Name"),
            "Model": document.get("Model"),
            "Manufacturer": document.get("Manufacturer"),
            "SerialNumber": document.get("SerialNumber"),
            "PowerState": document.get("PowerState"),
            "Health": status.get("Health"),
        }
        if not all(isinstance(value, str) and value for value in required.values()):
            raise RedfishProtocolError("Redfish ComputerSystem is missing required text")
        return RedfishSystem(
            system_id=str(required["Id"]),
            name=str(required["Name"]),
            model=str(required["Model"]),
            manufacturer=str(required["Manufacturer"]),
            serial_number=str(required["SerialNumber"]),
            power_state=str(required["PowerState"]),
            health=str(required["Health"]),
        )
