"""GraphQL query, mutation, and subscription boundary for relay tasks."""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator, Iterable
from dataclasses import dataclass
from typing import Any, cast

from graphql import (
    ExecutionResult,
    GraphQLError,
    GraphQLResolveInfo,
    build_schema,
    graphql_sync,
    parse,
    subscribe,
)

_SCHEMA = """
enum TaskState {
  QUEUED
  RUNNING
  SUCCEEDED
  FAILED
}

type Task {
  id: ID!
  action: String!
  state: TaskState!
}

type TaskEdge {
  cursor: String!
  node: Task!
}

type PageInfo {
  endCursor: String
  hasNextPage: Boolean!
}

type TaskConnection {
  edges: [TaskEdge!]!
  pageInfo: PageInfo!
}

type Query {
  task(id: ID!): Task
  tasks(first: Int! = 20, after: String): TaskConnection!
}

type Mutation {
  submitTask(id: ID!, action: String!): Task!
}

type Subscription {
  taskEvents(after: Int! = 0): Task!
}
"""


@dataclass(frozen=True, slots=True)
class TaskRecord:
    """One GraphQL representation of the shared relay task."""

    id: str
    action: str
    state: str
    sequence: int


class BatchTaskLoader:
    """One-request loader that replaces one lookup per GraphQL field."""

    def __init__(self, records: dict[str, TaskRecord]) -> None:
        self._records = records
        self.batch_calls = 0

    def load_many(self, task_ids: Iterable[str]) -> list[TaskRecord | None]:
        self.batch_calls += 1
        return [self._records.get(task_id) for task_id in task_ids]


class RelayGraphQL:
    """In-memory GraphQL service with explicit authorization scopes."""

    def __init__(self) -> None:
        self._records: dict[str, TaskRecord] = {}
        self._events: list[TaskRecord] = []
        self._next_sequence = 0
        self.schema = build_schema(_SCHEMA)
        query = self.schema.get_type("Query")
        mutation = self.schema.get_type("Mutation")
        subscription_type = self.schema.get_type("Subscription")
        if query is None or mutation is None or subscription_type is None:
            raise RuntimeError("relay GraphQL schema is incomplete")
        cast(Any, query).fields["task"].resolve = self._resolve_task
        cast(Any, query).fields["tasks"].resolve = self._resolve_tasks
        cast(Any, mutation).fields["submitTask"].resolve = self._resolve_submit
        cast(Any, subscription_type).fields["taskEvents"].subscribe = self._subscribe_events
        cast(Any, subscription_type).fields["taskEvents"].resolve = (
            lambda event, _info, **_arguments: event
        )

    def execute(
        self,
        document: str,
        *,
        variables: dict[str, object] | None = None,
        scopes: frozenset[str] = frozenset(),
    ) -> ExecutionResult:
        """Execute a query or mutation with request-scoped authorization."""

        return graphql_sync(
            self.schema,
            document,
            variable_values=variables,
            context_value={"scopes": scopes},
        )

    async def subscribe(
        self,
        document: str,
        *,
        variables: dict[str, object] | None = None,
        scopes: frozenset[str] = frozenset(),
    ) -> AsyncIterator[ExecutionResult]:
        """Start a GraphQL subscription over the retained event sequence."""

        result = await subscribe(
            self.schema,
            parse(document),
            variable_values=variables,
            context_value={"scopes": scopes},
        )
        if isinstance(result, ExecutionResult):
            raise GraphQLError(str(result.errors))
        return result

    def batch_loader(self) -> BatchTaskLoader:
        """Create a request-scoped loader for avoiding N+1 lookups."""

        return BatchTaskLoader(self._records)

    def _resolve_task(
        self,
        _root: object,
        info: GraphQLResolveInfo,
        *,
        id: str,
    ) -> TaskRecord | None:
        _require_scope(info, "tasks:read")
        return self._records.get(id)

    def _resolve_tasks(
        self,
        _root: object,
        info: GraphQLResolveInfo,
        *,
        first: int,
        after: str | None = None,
    ) -> dict[str, object]:
        _require_scope(info, "tasks:read")
        if first < 1 or first > 100:
            raise GraphQLError("first must be between 1 and 100")
        offset = _decode_cursor(after) + 1 if after is not None else 0
        records = list(self._records.values())
        selected = records[offset : offset + first]
        edges = [
            {"cursor": _encode_cursor(index), "node": record}
            for index, record in enumerate(selected, start=offset)
        ]
        end_index = offset + len(selected) - 1
        return {
            "edges": edges,
            "pageInfo": {
                "endCursor": _encode_cursor(end_index) if selected else None,
                "hasNextPage": offset + len(selected) < len(records),
            },
        }

    def _resolve_submit(
        self,
        _root: object,
        info: GraphQLResolveInfo,
        *,
        id: str,
        action: str,
    ) -> TaskRecord:
        _require_scope(info, "tasks:write")
        if not id.startswith("task-") or not id[5:].isdigit() or id[5:].startswith("0"):
            raise GraphQLError("id must match task-<positive integer>")
        if not action.strip():
            raise GraphQLError("action must not be empty")
        existing = self._records.get(id)
        if existing is not None:
            if existing.action != action:
                raise GraphQLError("task id is already bound to another action")
            return existing
        self._next_sequence += 1
        record = TaskRecord(id=id, action=action, state="QUEUED", sequence=self._next_sequence)
        self._records[id] = record
        self._events.append(record)
        return record

    async def _subscribe_events(
        self,
        _root: object,
        info: GraphQLResolveInfo,
        *,
        after: int = 0,
    ) -> AsyncIterator[TaskRecord]:
        _require_scope(info, "tasks:read")
        for event in self._events:
            if event.sequence > after:
                yield event


def _require_scope(info: GraphQLResolveInfo, required: str) -> None:
    context = cast(dict[str, frozenset[str]], info.context)
    if required not in context["scopes"]:
        raise GraphQLError(f"missing required scope: {required}")


def _encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(f"task:{offset}".encode()).decode()


def _decode_cursor(cursor: str) -> int:
    try:
        decoded = base64.urlsafe_b64decode(cursor.encode()).decode()
        prefix, raw_offset = decoded.split(":", maxsplit=1)
        if prefix != "task":
            raise ValueError
        return int(raw_offset)
    except (ValueError, UnicodeDecodeError) as exc:
        raise GraphQLError("after is not a relay cursor") from exc
