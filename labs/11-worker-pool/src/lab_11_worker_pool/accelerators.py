"""Capability-detected PCI device topology and placement helpers.

These functions read the same kind of Linux PCI and IOMMU sysfs trees that
`numa.py` reads for CPU nodes; nothing here calls a vendor GPU runtime.
`PciFunction` is the general record produced by walking that tree: a bus
address, a NUMA affinity hint inherited from the root complex it hangs off,
an IOMMU isolation group, and the ancestor bridges that help decide whether
a peer function sits close enough for direct device-to-device DMA.
`AcceleratorDevice` names that same record once its class code has already
been confirmed to be a GPU or dedicated accelerator; a network adapter
discovered the same way is a `PciFunction` with a network-controller class
code, never an `AcceleratorDevice`, because it was never filtered as one.
Bridge ancestry is only ever a topology estimate here; PCIe Access Control
Services, platform routing tables and firmware policy can each still
override what the ancestry alone would suggest, as the functions below note
where that applies. The functions below model those boundaries as plain data
so a placement or a readiness check can be tested with a fixture tree rather
than real hardware.
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

    This covers an ordinary Ethernet NIC as well as an InfiniBand or
    RoCE-capable host channel adapter; all report PCI base class 0x02. It
    exists so a network endpoint can be discovered and typed on its own
    terms rather than reused from the accelerator-only discovery path.
    """

    normalized = class_code.strip().lower().removeprefix("0x")
    return normalized[:2] == _NETWORK_CLASS_PREFIX


@dataclass(frozen=True, slots=True)
class PciFunction:
    """One PCI function discovered under a sysfs device tree.

    This record makes no claim about what kind of device the function is;
    a GPU, a network adapter and a storage controller all produce the same
    shape. `AcceleratorDevice` is this same type under a name that signals
    the class code has already been checked and confirmed to be an
    accelerator.
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


# A GPU or dedicated accelerator is a PciFunction whose class code has been
# confirmed by `is_accelerator_class`; the topology and placement math below
# is identical for any PCI function, so this is a naming alias, not a
# separate type a caller needs to convert between.
AcceleratorDevice = PciFunction


@dataclass(frozen=True, slots=True)
class PciTopology:
    """PCI functions of one class discovered under one PCI sysfs tree."""

    devices: tuple[PciFunction, ...]


# The result of `discover_accelerator_topology` specifically, kept as its
# own name for readability at call sites that only ever expect GPUs or
# dedicated accelerators, such as `plan_accelerator_workers`.
AcceleratorTopology = PciTopology


def discover_pci_topology(
    root: Path = Path("/sys/devices"),
    *,
    class_filter: Callable[[str], bool] = is_accelerator_class,
) -> PciTopology:
    """Walk a PCI device tree and collect functions matching a class filter.

    The tree mirrors what Linux exposes under `/sys/devices/pciDOMAIN:BUS`:
    root ports and switches nest as directories, and each PCI function is a
    directory carrying `vendor`, `device`, `class` and `numa_node` files. A
    directory without those files is a bridge; it never becomes a device,
    but its bus address still contributes to the ancestor path later used
    for locality decisions. `discover_accelerator_topology` and
    `discover_network_topology` are this function with `class_filter` fixed
    to `is_accelerator_class` and `is_network_class` respectively; call this
    one directly for any other class of interest.
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

    This is the typed discovery path for the network-adapter side of a
    transfer plan. It reads the same sysfs tree as
    `discover_accelerator_topology`, filtered to `is_network_class` instead,
    so a NIC or host channel adapter is discovered and typed on its own
    terms rather than reused from a code path that has already committed to
    treating its result as an accelerator.
    """

    return discover_pci_topology(root, class_filter=is_network_class)


def discover_iommu_group(
    address: PciAddress,
    root: Path = Path("/sys/kernel/iommu_groups"),
) -> tuple[str, ...]:
    """Return sibling PCI addresses sharing one IOMMU isolation group.

    The IOMMU translates a device's bus address to a physical address
    before a DMA engine reaches memory, and Linux's VFIO framework groups
    together every function whose transactions the platform cannot fully
    separate from one another. Every function inside one group must be
    assigned together; a device that shares a group with an unrelated
    function cannot be isolated on its own. Group membership is necessary
    information for planning a safe passthrough, not a certificate that a
    single-device group is automatically safe to hand to an unprivileged
    workload: that also depends on the guest or container's own driver and
    kernel confinement, and on device firmware neither the IOMMU nor this
    function inspects.
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

    This checks only whether two PCI functions share enough bridge
    ancestry to be topologically close, which is a necessary condition for
    peer-to-peer DMA, not a sufficient one. Neither argument needs to be an
    accelerator specifically; the same adjacency estimate applies to any
    pair of PCI functions, which is why a network adapter is accepted here
    as the general `PciFunction` record it actually is, not as an
    `AcceleratorDevice` it never was classified to be. Even directly under
    the same switch, a downstream port with PCIe Access Control Services
    (ACS) enabled redirects a peer-to-peer transaction up to the root
    complex for IOMMU checking rather than letting the switch route it
    directly; pass `acs_redirect_enabled=True` when that is known to be the
    case. Platform routing tables and firmware policy can each still block
    a path this estimate calls feasible, so confirm the real path on
    hardware before relying on it.
    """

    if acs_redirect_enabled:
        return False
    if first.ancestors and second.ancestors and first.ancestors[0] != second.ancestors[0]:
        return False
    return shared_ancestor_depth(first, second) >= min_shared_bridges


@dataclass(frozen=True, slots=True)
class TransferPlan:
    """Whether a GPU-NIC transfer can bypass host memory or must stage."""

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

    A peer-to-peer path moves data straight from the network adapter's DMA
    engine into device memory, which is the on-board memory a GPU exposes as
    its own separate address space rather than ordinary host RAM. When the
    topology estimate cannot support that path, the fallback reads into a
    page-locked host-pinned buffer on the NIC's own NUMA node, then issues a
    second DMA into device memory. Host-pinned memory exists for exactly
    this case: pages a DMA engine can address directly because the kernel
    has promised not to move or swap them mid-transfer. This is still only
    an estimate; see `peer_to_peer_feasible` for what it does and does not
    check.
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

    RoCEv2 carries RDMA transport over ordinary UDP and Ethernet, so a site
    running it must decide, on purpose, whether priority flow control and
    explicit congestion notification are part of its design, the approach
    DCQCN popularized, or whether it instead accepts an ordinary lossy
    Ethernet fabric and relies on the network adapter's own retransmission,
    the approach the Improved RoCE NIC design demonstrated works without
    priority flow control at all. Either way, that decision is specifically
    an Ethernet congestion policy, because RoCEv2's transport is Ethernet.
    Native InfiniBand's credit-based, link-level flow control is built into
    the fabric itself, so it never needs that particular decision; it is not
    carried over Ethernet, so there is no Ethernet congestion policy for it
    to have. That is a narrower claim than saying InfiniBand needs no
    congestion engineering at all: sizing buffer credits and switch
    bisection bandwidth for the traffic pattern in use is still real work an
    InfiniBand operator has to do, just not through PFC, ECN or an MTU
    chosen for Ethernet congestion behavior.
    """

    return fabric is FabricKind.ROCE_V2


@dataclass(frozen=True, slots=True)
class RoceCongestionPolicy:
    """One site's chosen congestion-control design for a RoCEv2 fabric.

    RoCEv2 mandates neither priority flow control, nor explicit congestion
    notification, nor a minimum MTU. A site running the DCQCN-style design
    commonly requires the first two and tunes an MTU to match its switch
    buffer thresholds. A site running a lossy, retransmission-based design
    such as IRN can reasonably require neither flow-control mechanism and
    rely on the adapter's own recovery path instead. This dataclass records
    whichever design a site has actually adopted rather than assuming the
    DCQCN choices are a universal protocol requirement.
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

    This checks a fabric against the congestion-control design a site has
    actually chosen; it does not treat priority flow control, explicit
    congestion notification or any particular MTU as a fixed protocol rule.
    A site running a lossy, retransmission-based design can supply a policy
    that requires none of the flow-control settings, and this function will
    not report a problem for their absence. None of these problems show up
    as a connection failure. They show up later, as a retransmission stall
    under load or as a priority flow control watchdog disabling a queue
    after sustained congestion, so a deterministic check against the site's
    own policy is worth more than a live probe that only exercises the idle
    path.
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
    """One worker's assigned accelerator and the NUMA node it satisfies."""

    worker_index: int
    device_address: str
    numa_node: int


def plan_accelerator_workers(
    topology: AcceleratorTopology,
    workers: int,
    *,
    single_numa_node: bool = True,
) -> tuple[AcceleratorWorkerPlacement, ...]:
    """Assign one accelerator per worker, modeling the kubelet Topology Manager.

    The Kubernetes device plugin API advertises accelerators as extended
    resources that a pod spec requests by count; kubelet's Topology Manager
    then aligns CPU, memory and device resources to the same NUMA node
    before admitting the pod. The `single-numa-node` policy this function
    defaults to rejects a request it cannot satisfy from one node rather
    than spreading it across nodes and accepting the cross-node latency
    silently. Passing `single_numa_node=False` models the more permissive
    `none` policy, which admits the pod without any alignment guarantee.
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
