"""Tests for the lab_14_native_extension package."""

from __future__ import annotations

from types import ModuleType

import pytest

from lab_14_native_extension import (
    HEADER_SIZE,
    MAX_FRAME_SIZE,
    RelayTask,
    TaskState,
    __version__,
    benchmark_frame_digests,
    describe_frame,
    has_native_extension,
    parse_length_prefix,
    render_task_frame,
    resolve_implementation,
)
from lab_14_native_extension.pure import fletcher16


def test_task_payload_round_trip_preserves_state() -> None:
    task = RelayTask("task-10", "profile-framing", TaskState.RUNNING)

    decoded = RelayTask.from_payload(task.to_payload())

    assert decoded == task


def test_python_digest_matches_the_reference_checksum() -> None:
    task = RelayTask("task-11", "measure-frame", TaskState.SUCCEEDED)
    frame = render_task_frame(task)
    implementation = resolve_implementation(prefer_native=False)

    digest = describe_frame(frame, implementation=implementation)

    assert digest.implementation == "python"
    assert digest.payload_length == len(task.to_payload())
    assert digest.checksum == fletcher16(task.to_payload())
    assert parse_length_prefix(frame, maximum_frame_size=MAX_FRAME_SIZE) == len(task.to_payload())


def test_describe_frame_rejects_short_or_truncated_inputs() -> None:
    implementation = resolve_implementation(prefer_native=False)

    with pytest.raises(ValueError, match="header requires 4 bytes"):
        parse_length_prefix(b"\x00\x01")
    with pytest.raises(ValueError, match="payload truncated"):
        describe_frame(b"\x00\x00\x00\x05abc", implementation=implementation)
    with pytest.raises(ValueError, match="maximum size"):
        parse_length_prefix((MAX_FRAME_SIZE + 1).to_bytes(4, "big"))


def test_loader_falls_back_to_python_when_native_module_is_missing() -> None:
    def missing_loader(name: str) -> ModuleType:
        raise ImportError(name)

    implementation = resolve_implementation(prefer_native=True, loader=missing_loader)

    assert implementation.name == "python"


def test_native_and_python_implementations_agree_on_the_same_corpus() -> None:
    frames = [
        render_task_frame(RelayTask("task-a", "queued-task", TaskState.QUEUED)),
        render_task_frame(RelayTask("task-b", "running-task", TaskState.RUNNING)),
        render_task_frame(RelayTask("task-c", "failed-task", TaskState.FAILED)),
    ]
    python_impl = resolve_implementation(prefer_native=False)
    preferred_impl = resolve_implementation(prefer_native=True)

    python_digests = [describe_frame(frame, implementation=python_impl) for frame in frames]
    preferred_digests = [describe_frame(frame, implementation=preferred_impl) for frame in frames]

    assert [digest.payload_length for digest in preferred_digests] == [
        digest.payload_length for digest in python_digests
    ]
    assert [digest.checksum for digest in preferred_digests] == [
        digest.checksum for digest in python_digests
    ]
    assert has_native_extension() is (preferred_impl.name == "native")


def test_benchmark_reports_comparable_measurements() -> None:
    frames = [
        render_task_frame(RelayTask("task-bench-1", "one", TaskState.QUEUED)),
        render_task_frame(RelayTask("task-bench-2", "two", TaskState.SUCCEEDED)),
    ]

    results = benchmark_frame_digests(frames, repeats=2)

    assert results
    assert results[0].frames == len(frames) * 2
    assert results[0].bytes_processed == sum(len(frame) - HEADER_SIZE for frame in frames) * 2
    assert all(result.elapsed_seconds >= 0.0 for result in results)
    assert len({result.digest_total for result in results}) == 1
    assert {result.implementation for result in results}.issubset({"python", "native"})


@pytest.mark.parametrize(
    ("task_id", "definition"),
    [("", "build-native"), ("task-10", "   ")],
)
def test_relay_task_validates_required_fields(task_id: str, definition: str) -> None:
    with pytest.raises(ValueError):
        RelayTask(task_id, definition)


def test_version_is_exposed() -> None:
    assert __version__
