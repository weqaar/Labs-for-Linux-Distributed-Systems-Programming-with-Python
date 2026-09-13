"""Tests for the lab_11_worker_pool package."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable
from types import SimpleNamespace

import pytest

from lab_11_worker_pool import (
    AcceleratorDevice,
    AcceleratorTopology,
    CpuWork,
    FabricKind,
    InMemoryVisibilityQueue,
    ManualClock,
    NumaNode,
    NumaTopology,
    PciFunction,
    RelayTask,
    RelayWorkerPool,
    RoceCongestionPolicy,
    RoceFabricConfig,
    TaskDisposition,
    TaskState,
    WorkerLostError,
    __version__,
    bind_numa_worker,
    count_deliveries_with_manager,
    cpu_transform,
    diagnose_roce_readiness,
    discover_accelerator_topology,
    discover_iommu_group,
    discover_network_topology,
    discover_numa_topology,
    discover_pci_topology,
    is_accelerator_class,
    is_network_class,
    numactl_command,
    parse_cpu_list,
    parse_numa_maps,
    parse_pci_address,
    peer_to_peer_feasible,
    pin_current_process,
    plan_accelerator_workers,
    plan_numa_workers,
    plan_transfer,
    requires_ethernet_congestion_policy,
    run_process_queue,
    sum_shared_bytes,
)


def wait_until(predicate: Callable[[], bool], timeout: float = 1.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_relay_task_requires_non_empty_fields() -> None:
    with pytest.raises(ValueError, match="task_id"):
        RelayTask("", "rebuild-index")
    with pytest.raises(ValueError, match="definition"):
        RelayTask("task-07", "   ")


def test_visibility_timeout_redelivers_an_unacknowledged_task() -> None:
    clock = ManualClock()
    queue = InMemoryVisibilityQueue[RelayTask](visibility_timeout=30.0, clock=clock.now)
    task = RelayTask("task-07", "rebuild-index")

    queue.put(task)
    first_lease = queue.reserve(timeout=0.0)
    assert first_lease is not None
    assert first_lease.delivery_count == 1
    assert queue.visible_count() == 0

    clock.advance(31.0)
    second_lease = queue.reserve(timeout=0.0)
    assert second_lease is not None
    assert second_lease.item == task
    assert second_lease.delivery_count == 2

    queue.acknowledge(second_lease.lease_id)
    assert queue.pending_count() == 0


def test_worker_pool_redelivers_a_lost_task() -> None:
    attempts: list[int] = []

    def handler(task: RelayTask, delivery_count: int) -> TaskDisposition:
        attempts.append(delivery_count)
        if delivery_count == 1:
            raise WorkerLostError(task.task_id)
        return TaskDisposition.SUCCEEDED

    queue = InMemoryVisibilityQueue[RelayTask](visibility_timeout=0.05)
    pool = RelayWorkerPool(queue, handler, concurrency=1, poll_interval=0.01)
    pool.submit(RelayTask("task-08", "compact-queue"))
    pool.start()

    assert wait_until(lambda: pool.snapshot("task-08").state is TaskState.SUCCEEDED)

    pool.request_shutdown()
    assert pool.join(1.0)
    assert attempts == [1, 2]
    assert pool.snapshot("task-08").deliveries == 2


def test_worker_pool_caps_the_number_of_in_flight_tasks() -> None:
    started = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    current = 0
    peak = 0
    started_count = 0

    def handler(task: RelayTask, delivery_count: int) -> TaskDisposition:
        nonlocal current, peak, started_count
        del task, delivery_count
        with lock:
            current += 1
            peak = max(peak, current)
            started_count += 1
            if started_count == 2:
                started.set()
        try:
            assert release.wait(1.0)
        finally:
            with lock:
                current -= 1
        return TaskDisposition.SUCCEEDED

    queue = InMemoryVisibilityQueue[RelayTask](visibility_timeout=1.0)
    pool = RelayWorkerPool(queue, handler, concurrency=2, poll_interval=0.01)
    for task_number in range(4):
        pool.submit(RelayTask(f"task-{task_number}", f"job-{task_number}"))
    pool.start()

    assert started.wait(1.0)
    with lock:
        assert peak == 2

    release.set()
    assert wait_until(
        lambda: all(snapshot.state is TaskState.SUCCEEDED for snapshot in pool.list_tasks())
    )
    pool.request_shutdown()
    assert pool.join(1.0)


def test_sigterm_drain_finishes_current_task_without_leasing_a_new_one() -> None:
    first_started = threading.Event()
    finish_first = threading.Event()

    def handler(task: RelayTask, delivery_count: int) -> TaskDisposition:
        del delivery_count
        if task.task_id == "task-drain-1":
            first_started.set()
            assert finish_first.wait(1.0)
        return TaskDisposition.SUCCEEDED

    queue = InMemoryVisibilityQueue[RelayTask](visibility_timeout=1.0)
    pool = RelayWorkerPool(queue, handler, concurrency=1, poll_interval=0.01)
    pool.submit(RelayTask("task-drain-1", "ship-batch"))
    pool.submit(RelayTask("task-drain-2", "ship-batch"))
    pool.start()

    assert first_started.wait(1.0)
    pool.request_shutdown()
    finish_first.set()

    assert pool.join(1.0)
    assert pool.snapshot("task-drain-1").state is TaskState.SUCCEEDED
    assert pool.snapshot("task-drain-2").state is TaskState.QUEUED
    assert queue.pending_count() == 1
    assert queue.visible_count() == 1


def test_spawned_processes_exchange_pickled_work_over_bounded_queues() -> None:
    work = (
        CpuWork("task-10", 10, rounds=100),
        CpuWork("task-11", 11, rounds=100),
        CpuWork("task-12", 12, rounds=100),
    )

    run = run_process_queue(work, workers=2)

    assert run.start_method == "spawn"
    assert len(run.started_worker_pids) == 2
    assert all(pid != os.getpid() for pid in run.started_worker_pids)
    assert tuple(result.task_id for result in run.results) == (
        "task-10",
        "task-11",
        "task-12",
    )
    assert tuple(result.value for result in run.results) == tuple(
        cpu_transform(item.seed, item.rounds) for item in work
    )
    assert all(result.worker_pid in run.started_worker_pids for result in run.results)


def test_manager_proxies_share_delivery_counts_between_processes() -> None:
    counts = count_deliveries_with_manager(
        ("task-10", "task-10", "task-11", "task-12"),
        workers=2,
    )

    assert counts == {"task-10": 2, "task-11": 1, "task-12": 1}


def test_shared_memory_avoids_copying_the_complete_payload_per_worker() -> None:
    payload = bytes(range(64)) * 128

    run = sum_shared_bytes(payload, workers=3)

    assert run.byte_sum == sum(payload)
    assert run.segment_size == len(payload)
    assert len(run.worker_pids) == 3
    assert all(pid != os.getpid() for pid in run.worker_pids)


def test_multicore_inputs_are_bounded_and_explicit() -> None:
    with pytest.raises(ValueError, match="task_id"):
        CpuWork("", 1)
    with pytest.raises(ValueError, match="rounds"):
        CpuWork("task-10", 1, rounds=0)
    with pytest.raises(ValueError, match="workers"):
        run_process_queue((), workers=0)
    with pytest.raises(ValueError, match="payload"):
        sum_shared_bytes(b"", workers=1)


def test_numa_topology_is_read_from_sysfs_contract(tmp_path) -> None:
    for node, cpus, distance in ((0, "0-1,4", "10 21"), (1, "2-3", "21 10")):
        directory = tmp_path / f"node{node}"
        directory.mkdir()
        (directory / "cpulist").write_text(cpus, encoding="ascii")
        (directory / "distance").write_text(distance, encoding="ascii")

    topology = discover_numa_topology(tmp_path)

    assert topology.is_numa
    assert topology.nodes[0].cpus == (0, 1, 4)
    assert topology.nodes[1].distances == (21, 10)


def test_numa_maps_and_numactl_plan_keep_memory_placement_visible() -> None:
    maps = """
00400000 default file=/usr/bin/python mapped=2 N0=2 kernelpagesize_kB=4
7f000000 bind:1 anon=9 dirty=9 N1=9 kernelpagesize_kB=4
7f100000 default anon=3 dirty=3 N0=1 N1=2 kernelpagesize_kB=4
"""

    assert parse_cpu_list("0-2,5,8-9") == (0, 1, 2, 5, 8, 9)
    assert parse_numa_maps(maps) == {0: 3, 1: 11}
    assert numactl_command(1, "lab_11_worker_pool.multicore") == (
        "numactl",
        "--cpunodebind=1",
        "--membind=1",
        "python",
        "-m",
        "lab_11_worker_pool.multicore",
    )


def test_numa_workers_balance_nodes_and_respect_allowed_cpus() -> None:
    planned = plan_numa_workers(
        NumaTopology(
            nodes=(
                NumaNode(0, (0, 1), (10, 20)),
                NumaNode(1, (2, 3), (20, 10)),
            ),
            allowed_cpus=(1, 2, 3),
        ),
        workers=5,
    )

    assert tuple((item.node_id, item.cpu_id) for item in planned) == (
        (0, 1),
        (1, 2),
        (0, 1),
        (1, 3),
        (0, 1),
    )


def test_cpu_and_memory_binding_happen_before_worker_allocation() -> None:
    affinity_calls: list[tuple[int, set[int]]] = []
    policy_calls: list[tuple[str, int | None]] = []
    binding = SimpleNamespace(
        info=SimpleNamespace(numa_available=lambda: True),
        schedule=SimpleNamespace(run_on_nodes=lambda node: policy_calls.append(("cpu-node", node))),
        memory=SimpleNamespace(set_local_alloc=lambda: policy_calls.append(("local-memory", None))),
    )

    pin_current_process(
        3,
        get_affinity=lambda _pid: {2, 3},
        set_affinity=lambda pid, cpus: affinity_calls.append((pid, cpus)),
    )
    bind_numa_worker(1, binding=binding)

    assert affinity_calls == [(0, {3})]
    assert policy_calls == [("cpu-node", 1), ("local-memory", None)]


def _write_pci_function(
    directory,
    *,
    vendor: str,
    device: str,
    class_code: str,
    numa_node: int | None,
) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "vendor").write_text(vendor, encoding="ascii")
    (directory / "device").write_text(device, encoding="ascii")
    (directory / "class").write_text(class_code, encoding="ascii")
    if numa_node is not None:
        (directory / "numa_node").write_text(str(numa_node), encoding="ascii")


def test_pci_address_round_trips_through_the_sysfs_bdf_format() -> None:
    address = parse_pci_address("0000:3d:00.0")

    assert (address.domain, address.bus, address.device, address.function) == (0, 0x3D, 0, 0)
    assert str(address) == "0000:3d:00.0"

    with pytest.raises(ValueError, match="domain:bus:device.function"):
        parse_pci_address("not-a-pci-address")


def test_accelerator_class_detection_covers_display_and_processing_codes() -> None:
    assert is_accelerator_class("0x030000")
    assert is_accelerator_class("120000")
    assert not is_accelerator_class("0x020000")


def test_network_class_detection_covers_ethernet_and_infiniband_codes() -> None:
    assert is_network_class("0x020000")
    assert is_network_class("0x020700")
    assert not is_network_class("0x030000")


def test_accelerator_topology_is_read_from_a_pci_sysfs_contract(tmp_path) -> None:
    root = tmp_path / "sys" / "devices"
    switch_a = root / "pci0000:00" / "0000:00:03.0" / "0000:04:00.0"
    switch_b = root / "pci0000:00" / "0000:00:03.0" / "0000:04:01.0"
    other_root = root / "pci0000:80" / "0000:80:01.0"
    _write_pci_function(
        switch_a, vendor="0x10de", device="0x2321", class_code="0x030000", numa_node=0
    )
    _write_pci_function(
        switch_b, vendor="0x15b3", device="0x101d", class_code="0x020700", numa_node=0
    )
    _write_pci_function(
        other_root, vendor="0x10de", device="0x2321", class_code="0x030000", numa_node=1
    )
    # A bridge directory with no vendor file must not become a device.
    (root / "pci0000:00" / "0000:00:03.0" / "empty_bridge").mkdir(parents=True)

    topology = discover_accelerator_topology(root)

    assert len(topology.devices) == 2
    gpu_zero = next(d for d in topology.devices if d.numa_node == 0)
    gpu_one = next(d for d in topology.devices if d.numa_node == 1)
    assert gpu_zero.has_numa_affinity
    assert str(gpu_zero.address) == "0000:04:00.0"
    assert gpu_zero.ancestors == ("pci0000:00", "0000:00:03.0")
    assert gpu_one.ancestors == ("pci0000:80",)


def test_network_topology_discovers_the_same_tree_filtered_to_network_class(tmp_path) -> None:
    root = tmp_path / "sys" / "devices"
    gpu = root / "pci0000:00" / "0000:00:03.0" / "0000:04:00.0"
    nic = root / "pci0000:00" / "0000:00:03.0" / "0000:04:01.0"
    _write_pci_function(gpu, vendor="0x10de", device="0x2321", class_code="0x030000", numa_node=0)
    _write_pci_function(nic, vendor="0x15b3", device="0x101d", class_code="0x020700", numa_node=0)

    accelerators = discover_accelerator_topology(root)
    networks = discover_network_topology(root)

    assert len(accelerators.devices) == 1
    assert str(accelerators.devices[0].address) == "0000:04:00.0"
    assert len(networks.devices) == 1
    assert str(networks.devices[0].address) == "0000:04:01.0"
    assert networks.devices[0].vendor_id == "0x15b3"

    everything = discover_pci_topology(root, class_filter=lambda _class_code: True)
    assert len(everything.devices) == 2


def test_accelerator_without_a_numa_node_file_reports_no_affinity(tmp_path) -> None:
    root = tmp_path / "sys" / "devices"
    directory = root / "pci0000:00" / "0000:00:02.0"
    _write_pci_function(
        directory, vendor="0x10de", device="0x2321", class_code="0x030000", numa_node=None
    )

    topology = discover_accelerator_topology(root)

    assert len(topology.devices) == 1
    assert topology.devices[0].numa_node == -1
    assert not topology.devices[0].has_numa_affinity


def test_iommu_group_lookup_returns_every_sibling_that_must_move_together(tmp_path) -> None:
    root = tmp_path / "iommu_groups"
    group = root / "42" / "devices"
    group.mkdir(parents=True)
    (group / "0000:04:00.0").touch()
    (group / "0000:04:00.1").touch()

    members = discover_iommu_group(parse_pci_address("0000:04:00.0"), root)

    assert members == ("0000:04:00.0", "0000:04:00.1")
    assert discover_iommu_group(parse_pci_address("0000:99:00.0"), root) == ()
    assert discover_iommu_group(parse_pci_address("0000:04:00.0"), tmp_path / "missing") == ()


def test_iommu_group_lookup_ignores_group_directories_without_a_devices_folder(
    tmp_path,
) -> None:
    root = tmp_path / "iommu_groups"
    (root / "1").mkdir(parents=True)
    group = root / "42" / "devices"
    group.mkdir(parents=True)
    (group / "0000:04:00.0").touch()

    members = discover_iommu_group(parse_pci_address("0000:04:00.0"), root)

    assert members == ("0000:04:00.0",)


def _gpu(address: str, *ancestors: str, numa_node: int = 0) -> AcceleratorDevice:
    return AcceleratorDevice(
        address=parse_pci_address(address),
        vendor_id="0x10de",
        device_id="0x2321",
        class_code="0x030000",
        numa_node=numa_node,
        ancestors=ancestors,
    )


def _nic(address: str, *ancestors: str, numa_node: int = 0) -> PciFunction:
    return PciFunction(
        address=parse_pci_address(address),
        vendor_id="0x15b3",
        device_id="0x1017",
        class_code="0x020000",
        numa_node=numa_node,
        ancestors=ancestors,
    )


def test_peer_to_peer_feasibility_needs_a_shared_switch_under_one_root_complex() -> None:
    gpu = _gpu("0000:04:00.0", "pci0000:00", "0000:00:03.0")
    nic_same_switch = _nic("0000:04:00.1", "pci0000:00", "0000:00:03.0")
    nic_other_switch = _nic("0000:05:00.0", "pci0000:00", "0000:00:04.0")
    nic_other_root = _nic("0000:84:00.0", "pci0000:80", "0000:80:01.0")

    assert peer_to_peer_feasible(gpu, nic_same_switch)
    assert not peer_to_peer_feasible(gpu, nic_other_root)
    assert not peer_to_peer_feasible(gpu, nic_other_switch, min_shared_bridges=2)


def test_acs_redirect_blocks_peer_to_peer_even_under_one_switch() -> None:
    gpu = _gpu("0000:04:00.0", "pci0000:00", "0000:00:03.0")
    nic_same_switch = _nic("0000:04:00.1", "pci0000:00", "0000:00:03.0")

    assert peer_to_peer_feasible(gpu, nic_same_switch)
    assert not peer_to_peer_feasible(gpu, nic_same_switch, acs_redirect_enabled=True)


def test_transfer_plan_stages_through_host_pinned_memory_across_root_complexes() -> None:
    gpu = _gpu("0000:04:00.0", "pci0000:00", "0000:00:03.0", numa_node=0)
    nic_same_switch = _nic("0000:04:00.1", "pci0000:00", "0000:00:03.0", numa_node=0)
    nic_other_root = _nic("0000:84:00.0", "pci0000:80", "0000:80:01.0", numa_node=1)

    direct = plan_transfer(gpu, nic_same_switch)
    staged = plan_transfer(gpu, nic_other_root)
    acs_staged = plan_transfer(gpu, nic_same_switch, acs_redirect_enabled=True)

    assert direct.mechanism == "peer_to_peer"
    assert direct.staging_numa_node is None
    assert staged.mechanism == "host_staged"
    assert staged.staging_numa_node == 1
    assert acs_staged.mechanism == "host_staged"
    assert acs_staged.staging_numa_node == 0


def test_infiniband_never_needs_an_ethernet_congestion_policy_but_roce_always_does() -> None:
    assert not requires_ethernet_congestion_policy(FabricKind.INFINIBAND)
    assert requires_ethernet_congestion_policy(FabricKind.ROCE_V2)


def test_roce_readiness_reports_every_control_a_dcqcn_style_policy_requires() -> None:
    dcqcn_style_policy = RoceCongestionPolicy(
        priority_flow_control_required=True,
        explicit_congestion_notification_required=True,
        minimum_mtu=4096,
    )
    broken = RoceFabricConfig(
        priority_flow_control_enabled=False,
        lossless_priority=9,
        ecn_enabled=False,
        mtu=1500,
    )

    problems = diagnose_roce_readiness(broken, dcqcn_style_policy)

    assert len(problems) == 3
    assert any("priority flow control" in problem for problem in problems)
    assert any("congestion notification" in problem for problem in problems)
    assert any("MTU 1500" in problem for problem in problems)

    invalid_priority = RoceFabricConfig(
        priority_flow_control_enabled=True,
        lossless_priority=9,
        ecn_enabled=True,
        mtu=4096,
    )
    priority_problems = diagnose_roce_readiness(invalid_priority, dcqcn_style_policy)
    assert priority_problems == ("lossless_priority must name one of the eight 802.1p classes",)

    ready = RoceFabricConfig(
        priority_flow_control_enabled=True,
        lossless_priority=3,
        ecn_enabled=True,
        mtu=4096,
    )
    assert diagnose_roce_readiness(ready, dcqcn_style_policy) == ()


def test_roce_readiness_allows_a_lossy_irn_style_policy_without_flow_control() -> None:
    lossy_policy = RoceCongestionPolicy(
        priority_flow_control_required=False,
        explicit_congestion_notification_required=False,
        minimum_mtu=1500,
    )
    lossy_fabric = RoceFabricConfig(
        priority_flow_control_enabled=False,
        lossless_priority=0,
        ecn_enabled=False,
        mtu=1500,
    )

    assert diagnose_roce_readiness(lossy_fabric, lossy_policy) == ()

    under_site_minimum = RoceFabricConfig(
        priority_flow_control_enabled=False,
        lossless_priority=0,
        ecn_enabled=False,
        mtu=1400,
    )
    problems = diagnose_roce_readiness(under_site_minimum, lossy_policy)
    assert problems == ("MTU 1400 is below this site's configured minimum of 1500 bytes",)


def test_single_numa_node_policy_rejects_a_request_no_node_can_satisfy() -> None:
    topology = AcceleratorTopology(
        devices=(
            _gpu("0000:04:00.0", "pci0000:00", numa_node=0),
            _gpu("0000:84:00.0", "pci0000:80", numa_node=1),
            _gpu("0000:84:00.1", "pci0000:80", numa_node=1),
        )
    )

    placements = plan_accelerator_workers(topology, 2, single_numa_node=True)
    assert all(p.numa_node == 1 for p in placements)
    assert tuple(p.device_address for p in placements) == ("0000:84:00.0", "0000:84:00.1")

    with pytest.raises(ValueError, match="single-numa-node policy"):
        plan_accelerator_workers(topology, 3, single_numa_node=True)


def test_none_policy_spreads_workers_across_nodes_without_alignment() -> None:
    topology = AcceleratorTopology(
        devices=(
            _gpu("0000:04:00.0", "pci0000:00", numa_node=0),
            _gpu("0000:84:00.0", "pci0000:80", numa_node=1),
        )
    )

    placements = plan_accelerator_workers(topology, 2, single_numa_node=False)

    assert tuple(p.numa_node for p in placements) == (0, 1)

    with pytest.raises(ValueError, match="only 2 accelerators"):
        plan_accelerator_workers(topology, 3, single_numa_node=False)

    with pytest.raises(ValueError, match="workers must be positive"):
        plan_accelerator_workers(topology, 0)


def test_version_is_exposed() -> None:
    assert __version__
