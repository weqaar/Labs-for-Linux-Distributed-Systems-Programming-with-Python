"""Small relay failure for pdb and post-mortem debugging exercises."""

from __future__ import annotations

import argparse
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TaskRecord:
    task_id: str
    attempts: int


def retry_delay(task: TaskRecord, base_seconds: float) -> float:
    """Calculate exponential delay while exposing invalid persisted state."""

    if task.attempts < 0:
        raise ValueError(f"{task.task_id} has a negative attempt count")
    return base_seconds * (2**task.attempts)


def load_task(record: dict[str, object]) -> TaskRecord:
    """Translate an untyped storage record at one explicit boundary."""

    attempts = record["attempts"]
    if not isinstance(attempts, (int, str)):
        raise TypeError("attempts must be an integer or decimal string")
    return TaskRecord(task_id=str(record["task_id"]), attempts=int(attempts))


def reproduce_invalid_retry() -> float:
    """Raise from a stable stack so readers can inspect each frame in pdb."""

    record: dict[str, object] = {"task_id": "task-17", "attempts": "-1"}
    return retry_delay(load_task(record), base_seconds=0.25)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--valid", action="store_true")
    arguments = parser.parse_args()
    if arguments.valid:
        print(retry_delay(TaskRecord("task-17", 2), 0.25))
    else:
        print(reproduce_invalid_retry())


if __name__ == "__main__":
    main()
