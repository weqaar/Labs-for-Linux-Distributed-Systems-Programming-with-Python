"""Tests for relay performance diagnostics."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pyperf
import pytest

from lab_07_performance_diagnostics import (
    DiagnosticPlan,
    RelayWorkload,
    __version__,
    benchmarks,
    compare_medians,
    cpu_work,
    inspect_bytecode,
    inspect_mapping,
    inspect_object,
    measure_call,
    measure_repeated,
    profile_call,
    profile_to_file,
    read_profile,
    summarize_result,
)
from lab_07_performance_diagnostics.debug_target import (
    TaskRecord,
    load_task,
    reproduce_invalid_retry,
    retry_delay,
)
from lab_07_performance_diagnostics.probe import wait_with_resources


class ManualClock:
    def __init__(self, values: tuple[int, ...]) -> None:
        self._values = iter(values)

    def __call__(self) -> int:
        return next(self._values)


def test_timing_uses_an_injected_monotonic_counter() -> None:
    result, timing = measure_call(sum, ManualClock((100, 145)), [1, 2, 3])

    assert result == 6
    assert timing.elapsed_ns == 45


def test_timing_rejects_a_clock_that_moves_backwards() -> None:
    with pytest.raises(ValueError, match="backwards"):
        measure_call(lambda: None, ManualClock((10, 9)))


def test_repeated_timing_retains_every_sample_and_reports_distribution() -> None:
    results, summary = measure_repeated(
        sum,
        ManualClock((0, 5, 10, 19, 20, 27)),
        3,
        [1, 2, 3],
    )

    assert results == (6, 6, 6)
    assert summary.samples_ns == (5, 9, 7)
    assert summary.minimum_ns == 5
    assert summary.median_ns == 7
    assert summary.maximum_ns == 9


def test_profile_identifies_the_profiled_relay_method() -> None:
    workload = RelayWorkload(["task-1", "task-2"])

    result, rows = profile_call(workload.classify_with_list, ["task-1", "task-9"])

    assert result == {"active": 1, "missing": 1}
    assert any("classify_with_list" in row.function for row in rows)


def test_saved_profile_can_be_read_in_cumulative_order(tmp_path: Path) -> None:
    output = tmp_path / "relay.prof"
    workload = RelayWorkload(["task-1", "task-2"])

    result = profile_to_file(output, workload.classify_with_set, ["task-1", "task-9"])
    rows = read_profile(output)

    assert result == {"active": 1, "missing": 1}
    assert output.is_file()
    assert any("classify_with_set" in row.function for row in rows)


@dataclass
class Inspectable:
    task_id: str

    def __call__(self, state: str) -> str:
        return f"{self.task_id}:{state}"


def test_interpreter_snapshot_covers_dir_vars_type_id_and_signature() -> None:
    value = Inspectable("task-7")

    snapshot = inspect_object(value)

    assert snapshot.type_name.endswith(".Inspectable")
    assert snapshot.identity == id(value)
    assert "__call__" in snapshot.attributes
    assert snapshot.instance_state == {"task_id": "'task-7'"}
    assert snapshot.call_signature == "(state: 'str') -> 'str'"


def test_bytecode_and_mapping_inspection_preserve_diagnostic_distinctions() -> None:
    rows = inspect_bytecode(retry_delay)

    assert any(row.operation == "RAISE_VARARGS" for row in rows)
    assert inspect_mapping({"deadline": None}, "deadline") == (True, None)
    assert inspect_mapping({}, "deadline") == (False, None)


def test_debug_target_has_valid_and_reproducibly_invalid_paths() -> None:
    assert retry_delay(TaskRecord("task-17", 2), 0.25) == 1.0
    assert load_task({"task_id": "task-8", "attempts": "3"}).attempts == 3
    with pytest.raises(TypeError, match="integer"):
        load_task({"task_id": "task-8", "attempts": object()})
    with pytest.raises(ValueError, match="task-17"):
        reproduce_invalid_retry()


def test_command_plans_distinguish_launch_attach_and_system_views() -> None:
    executable = Path("/tmp/relay-probe")

    assert DiagnosticPlan.gdb_run(executable, "--once").argv == (
        "gdb",
        "--args",
        "/tmp/relay-probe",
        "--once",
    )
    assert "-fno-omit-frame-pointer" in DiagnosticPlan.gdb_build(Path("probe.c"), executable).argv
    assert DiagnosticPlan.gdb_attach(42).argv == ("gdb", "-p", "42")
    assert DiagnosticPlan.strace_attach(42).argv[-2:] == ("-p", "42")
    assert DiagnosticPlan.strace_run("python", "-m", "relay").argv[-3:] == (
        "python",
        "-m",
        "relay",
    )
    assert DiagnosticPlan.lsof_process(42).argv == ("lsof", "-nP", "-p", "42")
    assert DiagnosticPlan.perf_record(42, 3).argv[-2:] == ("sleep", "3")
    assert DiagnosticPlan.perf_stat("python", "-m", "relay").argv[-3:] == (
        "python",
        "-m",
        "relay",
    )
    assert "function_graph" in DiagnosticPlan.ftrace_record(42).argv
    assert DiagnosticPlan.ftrace_report(Path("capture.dat")).argv[-1] == "capture.dat"
    assert DiagnosticPlan.proc_status(42)[0] == Path("/proc/42/status")


def test_command_plans_reject_invalid_pid_and_duration() -> None:
    with pytest.raises(ValueError, match="pid"):
        DiagnosticPlan.gdb_attach(0)
    with pytest.raises(ValueError, match="seconds"):
        DiagnosticPlan.perf_record(1, 0)
    with pytest.raises(ValueError, match="command"):
        DiagnosticPlan.strace_run()
    with pytest.raises(ValueError, match="command"):
        DiagnosticPlan.perf_stat()


def test_membership_implementations_have_the_same_product_result() -> None:
    workload = RelayWorkload(["task-1", "task-2", "task-3"])
    candidates = ["task-1", "task-9", "task-2"]

    assert workload.classify_with_list(candidates) == workload.classify_with_set(candidates)


def test_benchmark_effect_size_is_separate_from_a_required_product_change() -> None:
    comparison = compare_medians(0.010, 0.0095, required_change=10.0)

    assert comparison.speed_ratio == pytest.approx(1.0526, rel=1e-3)
    assert comparison.percent_change == pytest.approx(-5.0)
    assert not comparison.exceeds_required_change
    with pytest.raises(ValueError, match="positive"):
        compare_medians(0, 1, required_change=10)
    with pytest.raises(ValueError, match="cannot be negative"):
        compare_medians(1, 1, required_change=-1)


def test_pyperf_benchmark_functions_answer_two_different_questions() -> None:
    active = [f"task-{index}" for index in range(10)]

    assert benchmarks.benchmark_classification(1, use_set=False) > 0
    assert benchmarks.benchmark_classification(1, use_set=True) > 0
    assert benchmarks.benchmark_lookup(10, active, "task-9") > 0
    assert benchmarks.benchmark_lookup(10, set(active), "task-9") > 0


def test_pyperf_result_summary_keeps_run_counts_and_spread(tmp_path: Path) -> None:
    output = tmp_path / "bench.json"
    run = pyperf.Run(
        (0.001, 0.002),
        metadata={"name": "relay_lookup", "loops": 1},
        collect_metadata=False,
    )
    pyperf.BenchmarkSuite([pyperf.Benchmark([run])]).dump(str(output))

    summary = summarize_result(output)[0]

    assert summary.name == "relay_lookup"
    assert summary.runs == 1
    assert summary.values == 2
    assert summary.minimum_seconds == 0.001
    assert summary.maximum_seconds == 0.002
    assert summary.standard_deviation_seconds is not None


def test_cpu_probe_is_deterministic_and_validates_input() -> None:
    assert cpu_work(100) == cpu_work(100)
    with pytest.raises(ValueError, match="positive"):
        cpu_work(0)


def test_wait_probe_exposes_file_socket_and_pid(tmp_path: Path) -> None:
    marker = tmp_path / "probe.json"

    details = wait_with_resources(0.001, marker)

    assert isinstance(details["pid"], int)
    assert isinstance(details["port"], int)
    assert details["pid"] > 0
    assert details["port"] > 0
    assert marker.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="positive"):
        wait_with_resources(0, marker)


def test_version_is_exposed() -> None:
    assert __version__
