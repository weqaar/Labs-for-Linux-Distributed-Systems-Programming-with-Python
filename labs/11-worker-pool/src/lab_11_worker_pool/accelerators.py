"""Capability-detected PCI device topology and placement helpers.

Read Linux PCI and IOMMU sysfs trees without calling a vendor GPU runtime.
Discovery returns device addresses, class codes, NUMA hints and ancestor paths;
IOMMU group membership is queried separately. Tests can supply fixture trees.

Transfer plans estimate device locality, not whether DMA will work. PCIe
Access Control Services, routing and firmware can prevent a path that looks
suitable from its ancestor directories.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

_BDF = re.compile(
    r"^(?P<domain>[0-9a-fA-F]{4}):(?P<bus>[0-9a-fA-F]{2}):"
    r"(?P<device>[0-9a-fA-F]{2})\.(?P<function>[0-7])$"
)

# PCI class codes whose leading byte this module treats as an accelerator:
# 0x03 is a display controller, which is what most GPUs report even when
# used only for compute, and 0x12 is the processing-accelerator class used
# by dedicated AI and inference cards that carry no display output.
_ACCELERATOR_CLASS_PREFIXES = ("03", "12")

# 0x02 is the PCI base class for a network controller, covering ordinary
# Ethernet as well as InfiniBand and RoCE-capable host channel adapters.
_NETWORK_CLASS_PREFIX = "02"


@dataclass(frozen=True, slots=True)
class PciAddress:
    """One PCI domain:bus:device.function identifier."""

    domain: int
    bus: int
    device: int
    function: int

    def __str__(self) -> str:
        return f"{self.domain:04x}:{self.bus:02x}:{self.device:02x}.{self.function:x}"


def parse_pci_address(text: str) -> PciAddress:
    """Parse the sysfs directory name format for one PCI function."""

    match = _BDF.match(text.strip())
    if match is None:
        raise ValueError(f"not a PCI domain:bus:device.function address: {text!r}")
    return PciAddress(
        domain=int(match.group("domain"), 16),
        bus=int(match.group("bus"), 16),
        device=int(match.group("device"), 16),
        function=int(match.group("function"), 16),
    )


def is_accelerator_class(class_code: str) -> bool:
    """Return whether a PCI class code names a GPU or dedicated accelerator."""

    normalized = class_code.strip().lower().removeprefix("0x")
    return normalized[:2] in _ACCELERATOR_CLASS_PREFIXES


def is_network_class(class_code: str) -> bool:
    """Return whether a PCI class code names a network controller.

    PCI base class 0x02 includes Ethernet, InfiniBand and RoCE-capable network
    adapters. Use this filter rather than the accelerator filter to find them.
    """

    normalized = class_code.strip().lower().removeprefix("0x")
    return normalized[:2] == _NETWORK_CLASS_PREFIX


@dataclass(frozen=True, slots=True)
class PciFunction:
    """One PCI function discovered under a sysfs device tree.

    GPUs, network adapters and storage controllers use the same fields.
    Inspect class_code to distinguish them. AcceleratorDevice is an alias
    for this class, not a runtime check of the device kind.
    """

    address: PciAddress
    vendor_id: str
    device_id: str
    class_code: str
    numa_node: int
    ancestors: tuple[str, ...]

    @property
    def has_numa_affinity(self) -> bool:
        return self.numa_node >= 0


# The alias names accelerator-filtered results without requiring type conversion.
AcceleratorDevice = PciFunction


@dataclass(frozen=True, slots=True)
class PciTopology:
    """PCI functions of one class discovered under one PCI sysfs tree."""

    devices: tuple[PciFunction, ...]


# Placement callers use this alias for accelerator-filtered discovery results.
AcceleratorTopology = PciTopology


def discover_pci_topology(
    root: Path = Path("/sys/devices"),
    *,
    class_filter: Callable[[str], bool] = is_accelerator_class,
) -> PciTopology:
    """Walk a PCI device tree and collect functions matching a class filter.

    Under root, device directories must contain vendor, device and class
    files. Missing numa_node files produce a node value of -1. Directories
    without a vendor file are skipped as devices but remain in descendants'
    ancestor paths. A missing root returns an empty topology.

    The default filter selects accelerator class codes. Supply another
    class_filter, or use discover_network_topology, to select other devices.
    Read and parse errors in discovered files propagate to the caller.
    """

    devices: list[PciFunction] = []
    if root.is_dir():
        for path in sorted(root.rglob("*")):
            if not path.is_dir() or not (path / "vendor").is_file():
                continue
            class_code = (path / "class").read_text(encoding="ascii").strip()
            if not class_filter(class_code):
                continue
            vendor_id = (path / "vendor").read_text(encoding="ascii").strip()
            device_id = (path / "device").read_text(encoding="ascii").strip()
            numa_path = path / "numa_node"
            numa_node = (
                int(numa_path.read_text(encoding="ascii").strip()) if numa_path.is_file() else -1
            )
            ancestors = path.relative_to(root).parts[:-1]
            devices.append(
                PciFunction(
                    address=parse_pci_address(path.name),
                    vendor_id=vendor_id,
                    device_id=device_id,
                    class_code=class_code,
                    numa_node=numa_node,
                    ancestors=ancestors,
                )
            )
    return PciTopology(tuple(devices))


def discover_accelerator_topology(
    root: Path = Path("/sys/devices"),
) -> AcceleratorTopology:
    """Walk a PCI device tree and collect functions with an accelerator class.

    See `discover_pci_topology` for how the tree is walked; this fixes the
    class filter to `is_accelerator_class` so every returned function is a
    GPU or dedicated processing accelerator.
    """

    return discover_pci_topology(root, class_filter=is_accelerator_class)


def discover_network_topology(root: Path = Path("/sys/devices")) -> PciTopology:
    """Walk a PCI device tree and collect network controller functions.

    Use these records for the network-adapter input to plan_transfer.
    The traversal is shared with discover_accelerator_topology, but uses
    is_network_class rather than the accelerator class filter.
    """

    return discover_pci_topology(root, class_filter=is_network_class)


def discover_iommu_group(
    address: PciAddress,
    root: Path = Path("/sys/kernel/iommu_groups"),
) -> tuple[str, ...]:
    """Return sibling PCI addresses sharing one IOMMU isolation group.

    Read group membership below root and return sorted addresses, including
    the requested address. Return an empty tuple when no group is found.

    Linux VFIO treats a group as one isolation unit because the platform cannot
    fully separate its functions' transactions. Do not assign members to
    mutually untrusted owners. A single-device group alone does not establish
    safe passthrough: drivers, kernel confinement and firmware also matter,
    and this function checks none of them.
    """

    target = str(address)
    if not root.is_dir():
        return ()
    for group_dir in sorted(root.iterdir(), key=lambda entry: int(entry.name)):
        devices_dir = group_dir / "devices"
        if not devices_dir.is_dir():
            continue
        members = tuple(sorted(entry.name for entry in devices_dir.iterdir()))
        if target in members:
            return members
    return ()


def shared_ancestor_depth(first: PciFunction, second: PciFunction) -> int:
    """Count matching bridge ancestors two PCI functions share, root first."""

    depth = 0
    for left, right in zip(first.ancestors, second.ancestors):
        if left != right:
            break
        depth += 1
    return depth


def peer_to_peer_feasible(
    first: PciFunction,
    second: PciFunction,
    *,
    min_shared_bridges: int = 1,
    acs_redirect_enabled: bool = False,
) -> bool:
    """Estimate topology adjacency for a peer-to-peer DMA path.

    Accept any two PciFunction records. Return False for different recorded
    roots or known ACS redirection; otherwise compare the shared ancestor
    count with min_shared_bridges. These are directory-path comparisons, not
    measurements of a PCIe switch.

    Set acs_redirect_enabled when Access Control Services redirects peer
    transactions toward the root complex. A True result does not check device
    support, platform routing or firmware policy; verify the path on hardware.
    """

    if acs_redirect_enabled:
        return False
    if first.ancestors and second.ancestors and first.ancestors[0] != second.ancestors[0]:
        return False
    return shared_ancestor_depth(first, second) >= min_shared_bridges


@dataclass(frozen=True, slots=True)
class TransferPlan:
    """Estimated transfer mechanism and optional host-staging NUMA node."""

    mechanism: str
    staging_numa_node: int | None


def plan_transfer(
    gpu: PciFunction,
    nic: PciFunction,
    *,
    min_shared_bridges: int = 1,
    acs_redirect_enabled: bool = False,
) -> TransferPlan:
    """Choose between a peer-to-peer path and a host-staged path.

    Return peer_to_peer when the topology estimate passes. Otherwise return
    host_staged with nic.numa_node, which can be -1 when its locality is unknown.
    This function neither allocates memory nor initiates a transfer.

    A real peer transfer writes directly to device memory. Host staging instead
    uses a pinned host buffer, whose pages stay resident during DMA, followed
    by a second transfer to the device. See peer_to_peer_feasible for the
    estimate's limitations.
    """

    if peer_to_peer_feasible(
        gpu,
        nic,
        min_shared_bridges=min_shared_bridges,
        acs_redirect_enabled=acs_redirect_enabled,
    ):
        return TransferPlan(mechanism="peer_to_peer", staging_numa_node=None)
    return TransferPlan(mechanism="host_staged", staging_numa_node=nic.numa_node)


class FabricKind(Enum):
    """The two link layers this module plans an RDMA transport over."""

    INFINIBAND = "infiniband"
    ROCE_V2 = "roce_v2"


def requires_ethernet_congestion_policy(fabric: FabricKind) -> bool:
    """Return whether a fabric needs an explicit Ethernet congestion policy.

    Return True for RoCEv2, which carries RDMA over UDP/IP and Ethernet.
    Its operator must choose how to handle congestion, for example with
    priority flow control and explicit congestion notification, or with a
    loss-tolerant design such as Improved RoCE NIC (IRN).

    Return False for native InfiniBand because it does not use Ethernet.
    InfiniBand still needs congestion engineering, including buffer-credit
    and bandwidth planning; False does not mean that no policy is needed.
    """

    return fabric is FabricKind.ROCE_V2


@dataclass(frozen=True, slots=True)
class RoceCongestionPolicy:
    """One site's chosen congestion-control design for a RoCEv2 fabric.

    Supply the site's requirements for priority flow control, explicit
    congestion notification and minimum MTU in bytes. They are deployment
    choices, not universal RoCEv2 requirements. Data Center Quantized
    Congestion Notification (DCQCN) deployments commonly use both mechanisms;
    loss-tolerant designs such as IRN can use different requirements.
    """

    priority_flow_control_required: bool
    explicit_congestion_notification_required: bool
    minimum_mtu: int


@dataclass(frozen=True, slots=True)
class RoceFabricConfig:
    """Switch and adapter settings observed on one RoCEv2 path."""

    priority_flow_control_enabled: bool
    lossless_priority: int
    ecn_enabled: bool
    mtu: int


def diagnose_roce_readiness(
    config: RoceFabricConfig,
    policy: RoceCongestionPolicy,
) -> tuple[str, ...]:
    """List reasons an observed RoCEv2 path does not meet one site's policy.

    Compare supplied settings with policy and return a tuple of diagnostic
    strings, empty when the checked settings comply. Disabled flow-control
    mechanisms are not reported when the policy does not require them.

    No network probe runs. Passing these checks does not measure behavior under
    load; congestion problems can remain invisible to an idle connection test.
    """

    problems: list[str] = []
    if policy.priority_flow_control_required:
        if not config.priority_flow_control_enabled:
            problems.append(
                "this site's policy requires priority flow control for this "
                "RoCEv2 path, but it is disabled here"
            )
        elif not 0 <= config.lossless_priority <= 7:
            problems.append("lossless_priority must name one of the eight 802.1p classes")
    if policy.explicit_congestion_notification_required and not config.ecn_enabled:
        problems.append(
            "this site's policy requires explicit congestion notification for "
            "this RoCEv2 path, but it is disabled here"
        )
    if config.mtu < policy.minimum_mtu:
        problems.append(
            f"MTU {config.mtu} is below this site's configured minimum of "
            f"{policy.minimum_mtu} bytes"
        )
    return tuple(problems)


@dataclass(frozen=True, slots=True)
class AcceleratorWorkerPlacement:
    """One worker's assigned accelerator address and recorded NUMA node."""

    worker_index: int
    device_address: str
    numa_node: int


def plan_accelerator_workers(
    topology: AcceleratorTopology,
    workers: int,
    *,
    single_numa_node: bool = True,
) -> tuple[AcceleratorWorkerPlacement, ...]:
    """Assign one accelerator per worker using the supplied topology.

    With single_numa_node=True, select the first recorded node with enough
    devices, or raise ValueError. Otherwise select devices across nodes.
    Workers must be positive and no greater than the available device count.

    This illustrates one locality choice made by kubelet's Topology Manager,
    not its complete admission algorithm. It does not align CPUs or memory,
    reserve devices, or reject the unknown-node value -1. Callers needing
    known locality must check discovery results before planning.
    """

    if workers <= 0:
        raise ValueError("workers must be positive")

    by_node: dict[int, list[AcceleratorDevice]] = {}
    for device in topology.devices:
        by_node.setdefault(device.numa_node, []).append(device)

    if single_numa_node:
        for node_id in sorted(by_node):
            devices = by_node[node_id]
            if len(devices) >= workers:
                return tuple(
                    AcceleratorWorkerPlacement(index, str(devices[index].address), node_id)
                    for index in range(workers)
                )
        raise ValueError(
            f"no single NUMA node has {workers} accelerators available; "
            "the single-numa-node policy admits none of this request"
        )

    ordered = sorted(
        topology.devices,
        key=lambda device: (device.numa_node, str(device.address)),
    )
    if len(ordered) < workers:
        raise ValueError(f"topology has only {len(ordered)} accelerators for {workers} workers")
    return tuple(
        AcceleratorWorkerPlacement(index, str(ordered[index].address), ordered[index].numa_node)
        for index in range(workers)
    )
