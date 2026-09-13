"""Read-only Redfish inventory representation for the final SigRaft service."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ManagedSystem:
    """Safe hardware identity and health selected for application operators."""

    system_id: str
    name: str
    model: str
    manufacturer: str
    serial_number: str
    power_state: str
    health: str

    def as_redfish(self) -> dict[str, object]:
        """Render the selected ComputerSystem fields."""

        path = f"/redfish/v1/Systems/{self.system_id}"
        return {
            "@odata.id": path,
            "@odata.type": "#ComputerSystem.v1_22_0.ComputerSystem",
            "Id": self.system_id,
            "Name": self.name,
            "Model": self.model,
            "Manufacturer": self.manufacturer,
            "SerialNumber": self.serial_number,
            "PowerState": self.power_state,
            "Status": {"Health": self.health, "State": "Enabled"},
        }


class RedfishInventory:
    """Expose a bounded inventory subset without BMC control operations."""

    def __init__(self, systems: tuple[ManagedSystem, ...]) -> None:
        if not systems:
            raise ValueError("Redfish inventory requires at least one system")
        identifiers = [system.system_id for system in systems]
        if len(set(identifiers)) != len(identifiers):
            raise ValueError("Redfish system ids must be unique")
        if any(not identifier or "/" in identifier for identifier in identifiers):
            raise ValueError("Redfish system ids must be non-empty path segments")
        self._systems = systems
        self._by_id = {system.system_id: system for system in systems}

    def service_root(self) -> dict[str, object]:
        return {
            "@odata.id": "/redfish/v1/",
            "@odata.type": "#ServiceRoot.v1_18_0.ServiceRoot",
            "Id": "RootService",
            "Name": "SigRaft read-only data-centre inventory",
            "RedfishVersion": "1.20.0",
            "Systems": {"@odata.id": "/redfish/v1/Systems"},
        }

    def systems_collection(self) -> dict[str, object]:
        members = [
            {"@odata.id": f"/redfish/v1/Systems/{system.system_id}"} for system in self._systems
        ]
        return {
            "@odata.id": "/redfish/v1/Systems",
            "@odata.type": "#ComputerSystemCollection.ComputerSystemCollection",
            "Name": "Managed systems",
            "Members@odata.count": len(members),
            "Members": members,
        }

    def system(self, system_id: str) -> dict[str, object] | None:
        system = self._by_id.get(system_id)
        return system.as_redfish() if system is not None else None


def default_inventory() -> RedfishInventory:
    """Return deterministic local inventory for the runnable checkpoint."""

    return RedfishInventory(
        (
            ManagedSystem(
                system_id="sigraft-node-1",
                name="SigRaft compute node 1",
                model="Virtual rack node",
                manufacturer="SigRaft lab",
                serial_number="SIGRAFT-LOCAL-001",
                power_state="On",
                health="OK",
            ),
        )
    )
