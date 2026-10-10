"""Functional tests for framing relay jobs with the preferred implementation.

These are functional tests. They drive the public package interface of
``lab_14_native_extension``: render a job frame, describe it with the
implementation that ``resolve_implementation`` selects (native when the
extension is built, Python otherwise), decode the job again and run the
benchmark on the same frames. Every result is compared with the pure-Python
reference, and malformed frames must be rejected by both implementations.
"""

# pyright: strict

from __future__ import annotations

import pytest

import lab_14_native_extension as lab


def lifecycle_frames() -> list[bytes]:
    """Return one frame for each state that job task-17 passes through."""
    states = (
        lab.TaskState.QUEUED,
        lab.TaskState.RUNNING,
        lab.TaskState.SUCCEEDED,
        lab.TaskState.FAILED,
    )
    return [lab.render_task_frame(lab.RelayTask("task-17", "resize-image", s)) for s in states]


def test_job_frames_round_trip_with_matching_digests_on_both_paths() -> None:
    reference = lab.resolve_implementation(prefer_native=False)
    preferred = lab.resolve_implementation(prefer_native=True)

    for frame in lifecycle_frames():
        expected = lab.describe_frame(frame, implementation=reference)
        actual = lab.describe_frame(frame)
        payload = frame[lab.HEADER_SIZE :]

        assert actual.implementation == preferred.name
        assert (actual.payload_length, actual.checksum) == (
            expected.payload_length,
            expected.checksum,
        )
        assert lab.RelayTask.from_payload(payload).task_id == "task-17"

    assert [
        lab.RelayTask.from_payload(f[lab.HEADER_SIZE :]).state.value for f in lifecycle_frames()
    ] == [
        "queued",
        "running",
        "succeeded",
        "failed",
    ]


def test_benchmark_reports_the_implementation_that_actually_ran() -> None:
    frames = lifecycle_frames()

    results = lab.benchmark_frame_digests(frames, repeats=3)

    expected_names = ["python"] if not lab.has_native_extension() else ["python", "native"]
    assert [result.implementation for result in results] == expected_names
    assert len({result.digest_total for result in results}) == 1
    assert all(result.frames == len(frames) * 3 for result in results)


@pytest.mark.parametrize("prefer_native", [False, True])
def test_malformed_frames_are_rejected_by_each_implementation(prefer_native: bool) -> None:
    implementation = lab.resolve_implementation(prefer_native=prefer_native)
    frame = lifecycle_frames()[0]

    with pytest.raises(ValueError, match="truncated"):
        lab.describe_frame(frame[:-1], implementation=implementation)
    with pytest.raises(ValueError, match="maximum size"):
        lab.describe_frame(frame, maximum_frame_size=4, implementation=implementation)
    with pytest.raises(ValueError, match="maximum size"):
        lab.render_task_frame(lab.RelayTask("task-17", "x" * 64), maximum_frame_size=16)
