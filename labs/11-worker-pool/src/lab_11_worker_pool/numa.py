"""Capability-detected Linux NUMA topology and placement helpers."""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path

_NODE_PAGES = re.compile(r"\bN(?P<node>\d+)=(?P<pages>\d+)\b")


@dataclass(frozen=True, slots=True)
class NumaNode:
    """One Linux NUMA node and its logical CPUs."""

    node_id: int
    cpus: tuple[int, ...]
    distances: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class NumaTopology:
    """NUMA nodes visible through Linux sysfs."""

    nodes: tuple[NumaNode, ...]
    allowed_cpus: tuple[int, ...]

    @property
    def is_numa(self) -> bool:
        return len(self.nodes) > 1


@dataclass(frozen=True, slots=True)
class NumaWorkerPlacement:
    """One process worker's CPU and local-memory node."""

    worker_index: int
    node_id: int
    cpu_id: int


def parse_cpu_list(value: str) -> tuple[int, ...]:
    """Parse the range syntax used by Linux cpulist files."""

    cpus: list[int] = []
    for part in value.strip().split(","):
        if not part:
            continue
        bounds = part.split("-", maxsplit=1)
        if len(bounds) == 1:
            cpus.append(int(bounds[0]))
        else:
            start, end = (int(bound) for bound in bounds)
            if end < start:
                raise ValueError(f"invalid CPU range: {part}")
            cpus.extend(range(start, end + 1))
    return tuple(cpus)


def discover_numa_topology(
    root: Path = Path("/sys/devices/system/node"),
) -> NumaTopology:
    """Read NUMA nodes without requiring libnuma or elevated privileges."""

    nodes: list[NumaNode] = []
    if root.is_dir():
        for directory in sorted(root.glob("node[0-9]*"), key=_node_number):
            node_id = _node_number(directory)
            cpus = parse_cpu_list((directory / "cpulist").read_text(encoding="ascii"))
            distance_path = directory / "distance"
            distances = (
                tuple(int(value) for value in distance_path.read_text(encoding="ascii").split())
                if distance_path.is_file()
                else ()
            )
            nodes.append(NumaNode(node_id, cpus, distances))
    allowed = tuple(sorted(os.sched_getaffinity(0))) if hasattr(os, "sched_getaffinity") else ()
    return NumaTopology(tuple(nodes), allowed)


def parse_numa_maps(lines: str) -> dict[int, int]:
    """Count mapped pages per NUMA node from `/proc/PID/numa_maps` text."""

    pages: dict[int, int] = {}
    for match in _NODE_PAGES.finditer(lines):
        node = int(match.group("node"))
        pages[node] = pages.get(node, 0) + int(match.group("pages"))
    return pages


def numactl_command(node: int, module: str) -> tuple[str, ...]:
    """Return an explicit CPU and memory binding command."""

    if node < 0:
        raise ValueError("NUMA node must be non-negative")
    if not module:
        raise ValueError("module must not be empty")
    return (
        "numactl",
        f"--cpunodebind={node}",
        f"--membind={node}",
        "python",
        "-m",
        module,
    )


def plan_numa_workers(
    topology: NumaTopology,
    workers: int,
) -> tuple[NumaWorkerPlacement, ...]:
    """Balance workers across allowed NUMA nodes while rotating their CPUs."""

    if workers <= 0:
        raise ValueError("workers must be positive")
    allowed = set(topology.allowed_cpus)
    eligible = tuple(
        (node.node_id, tuple(cpu for cpu in node.cpus if not allowed or cpu in allowed))
        for node in topology.nodes
        if any(not allowed or cpu in allowed for cpu in node.cpus)
    )
    if not eligible:
        raise ValueError("topology has no CPUs allowed for this process")

    placements: list[NumaWorkerPlacement] = []
    for worker_index in range(workers):
        node_index = worker_index % len(eligible)
        node_id, cpus = eligible[node_index]
        rotation = worker_index // len(eligible)
        placements.append(NumaWorkerPlacement(worker_index, node_id, cpus[rotation % len(cpus)]))
    return tuple(placements)


def pin_current_process(
    cpu_id: int,
    *,
    get_affinity: Callable[[int], set[int]] | None = None,
    set_affinity: Callable[[int, set[int]], None] | None = None,
) -> None:
    """Restrict the calling process to one allowed Linux logical CPU."""

    if cpu_id < 0:
        raise ValueError("CPU ID must be non-negative")
    getter = get_affinity or getattr(os, "sched_getaffinity", None)
    setter = set_affinity or getattr(os, "sched_setaffinity", None)
    if getter is None or setter is None:
        raise RuntimeError("Linux CPU affinity is unavailable")
    allowed = getter(0)
    if cpu_id not in allowed:
        raise ValueError(f"CPU {cpu_id} is outside the allowed affinity set")
    setter(0, {cpu_id})


def bind_numa_worker(
    node_id: int,
    *,
    binding: object | None = None,
) -> None:
    """Bind a worker to a NUMA node and make later allocations local."""

    if node_id < 0:
        raise ValueError("NUMA node must be non-negative")
    numa_binding = binding if binding is not None else import_module("numa")
    info = getattr(numa_binding, "info", None)
    schedule = getattr(numa_binding, "schedule", None)
    memory = getattr(numa_binding, "memory", None)
    available = getattr(info, "numa_available", None)
    run_on_nodes = getattr(schedule, "run_on_nodes", None)
    set_local_alloc = getattr(memory, "set_local_alloc", None)
    if not callable(available):
        raise RuntimeError("installed numa binding does not expose numa_available")
    if not callable(run_on_nodes):
        raise RuntimeError("installed numa binding does not expose run_on_nodes")
    if not callable(set_local_alloc):
        raise RuntimeError("installed numa binding does not expose set_local_alloc")
    if not available():
        raise RuntimeError("libnuma reports that NUMA policy is unavailable")
    run_on_nodes(node_id)
    set_local_alloc()


def _node_number(path: Path) -> int:
    return int(path.name.removeprefix("node"))
