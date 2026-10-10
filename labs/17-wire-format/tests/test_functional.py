"""Functional tests for the lab's wire-format contracts.

These tests drive the public codec interface the way two relay processes on
different schema versions would use it: one side serializes bytes, the other
side decodes them with `decode_v1`, `decode_v2` or `decode_owner_patch`.
Each test follows one job message from the producer's bytes to the
consumer's view of that job.
"""

# pyright: strict

from __future__ import annotations

import pytest
from google.protobuf.message import DecodeError

from lab_17_wire_format.json_codec import OwnerOperation, OwnerPatch, decode_owner_patch
from lab_17_wire_format.pb import task_v1_pb2, task_v2_pb2
from lab_17_wire_format.protobuf_codec import decode_v1, decode_v2


def apply_owner_patch(current: str | None, patch: OwnerPatch) -> str | None:
    """Apply a decoded patch the way a job store would update its owner field."""
    if patch.operation is OwnerOperation.UNCHANGED:
        return current
    if patch.operation is OwnerOperation.CLEAR:
        return None
    return patch.value


def test_new_job_fields_survive_a_hop_through_an_old_relay() -> None:
    produced = task_v2_pb2.Task(id="task-17", action="index", owner="Ada", priority=7)

    relayed_by_old_reader = decode_v1(produced.SerializeToString())
    forwarded = relayed_by_old_reader.SerializeToString()
    consumed = decode_v2(forwarded)

    assert relayed_by_old_reader.id == "task-17"
    assert relayed_by_old_reader.action == "index"
    assert consumed.id == "task-17"
    assert consumed.action == "index"
    assert consumed.HasField("owner")
    assert consumed.owner == "Ada"
    assert consumed.priority == 7


def test_old_producer_job_reaches_new_consumer_with_owner_absent() -> None:
    produced = task_v1_pb2.Task(id="task-17", action="deliver")

    consumed = decode_v2(produced.SerializeToString())

    assert consumed.id == "task-17"
    assert consumed.action == "deliver"
    assert not consumed.HasField("owner")
    assert consumed.priority == 0


def test_owner_patch_sequence_sets_keeps_and_clears_the_owner() -> None:
    owner: str | None = None
    history: list[str | None] = []

    for body in (b'{"owner": "Ada"}', b"{}", b'{"owner": null}', b"{}"):
        owner = apply_owner_patch(owner, decode_owner_patch(body))
        history.append(owner)

    assert history == ["Ada", "Ada", None, None]


@pytest.mark.parametrize(
    "body",
    [b'{"owner": ""}', b'{"owner": 17}', b'{"ownr": "Ada"}', b"[]", b"{not json"],
)
def test_rejected_owner_patch_leaves_the_current_owner_in_place(body: bytes) -> None:
    owner: str | None = "Ada"

    with pytest.raises(ValueError):
        owner = apply_owner_patch(owner, decode_owner_patch(body))

    assert owner == "Ada"


def test_oversized_or_corrupt_job_message_is_rejected_by_both_readers() -> None:
    produced = task_v2_pb2.Task(id="task-17", action="index", owner="Ada").SerializeToString()
    limit = len(produced) - 1

    with pytest.raises(ValueError, match="exceeds"):
        decode_v1(produced, maximum=limit)
    with pytest.raises(ValueError, match="exceeds"):
        decode_v2(produced, maximum=limit)
    with pytest.raises(DecodeError):
        decode_v1(b"\x0f")
    with pytest.raises(DecodeError):
        decode_v2(produced[:-1])
