"""Version-skew tests for JSON and Protocol Buffers."""

from __future__ import annotations

from pathlib import Path

import pytest
from google.protobuf.message import DecodeError

from lab_17_wire_format.json_codec import OwnerOperation, decode_owner_patch
from lab_17_wire_format.pb import task_v1_pb2, task_v2_pb2
from lab_17_wire_format.protobuf_codec import decode_v1, decode_v2

ROOT = Path(__file__).parents[1]


@pytest.mark.parametrize(
    ("body", "operation", "value"),
    [
        (b"{}", OwnerOperation.UNCHANGED, None),
        (b'{"owner": null}', OwnerOperation.CLEAR, None),
        (b'{"owner": "Ada"}', OwnerOperation.SET, "Ada"),
    ],
)
def test_json_preserves_absent_null_and_value(
    body: bytes,
    operation: OwnerOperation,
    value: str | None,
) -> None:
    patch = decode_owner_patch(body)

    assert patch.operation is operation
    assert patch.value == value


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"[]", "must be an object"),
        (b"{", "invalid JSON"),
        (b'{"owner": ""}', "non-empty string"),
        (b'{"owner": false}', "non-empty string"),
        (b'{"owenr": "Ada"}', "unknown field: owenr"),
    ],
)
def test_json_rejects_invalid_contracts(body: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        decode_owner_patch(body)


def test_json_rejects_oversized_input_before_parsing() -> None:
    with pytest.raises(ValueError, match="exceeds 1 bytes"):
        decode_owner_patch(b"{}", maximum=1)


def test_new_protobuf_reader_accepts_an_old_message() -> None:
    old = task_v1_pb2.Task(id="task-17", action="index")

    received = decode_v2(old.SerializeToString())

    assert received.id == "task-17"
    assert received.action == "index"
    assert not received.HasField("owner")
    assert received.priority == 0


def test_old_reader_preserves_fields_it_does_not_know() -> None:
    new = task_v2_pb2.Task(id="task-17", action="index", owner="Ada", priority=7)

    old = decode_v1(new.SerializeToString())
    relayed = decode_v2(old.SerializeToString())

    assert old.id == "task-17"
    assert relayed.owner == "Ada"
    assert relayed.priority == 7


def test_optional_protobuf_scalar_preserves_presence() -> None:
    absent = task_v2_pb2.Task(id="task-17")
    present = task_v2_pb2.Task(id="task-17", owner="")

    assert not absent.HasField("owner")
    assert present.HasField("owner")


def test_protobuf_field_numbers_remain_stable() -> None:
    old_fields = task_v1_pb2.Task.DESCRIPTOR.fields_by_name
    new_fields = task_v2_pb2.Task.DESCRIPTOR.fields_by_name

    assert old_fields["id"].number == new_fields["id"].number == 1
    assert old_fields["action"].number == new_fields["action"].number == 2
    assert new_fields["owner"].number == 3


def test_removed_protobuf_identity_is_reserved() -> None:
    schema = (ROOT / "schemas/task_v2.proto").read_text()

    assert "reserved 5;" in schema
    assert 'reserved "legacy_queue";' in schema


def test_protobuf_rejects_oversized_or_invalid_input() -> None:
    with pytest.raises(ValueError, match="exceeds 1 bytes"):
        decode_v2(b"ab", maximum=1)
    with pytest.raises(DecodeError):
        decode_v2(b"\x0f")
