"""GraphQL boundary over the same SigRaft task service used by REST."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, cast

from graphql import ExecutionResult, GraphQLError, GraphQLResolveInfo, build_schema, graphql_sync


class TaskRecord(Protocol):
    """Fields shared by REST and GraphQL task representations."""

    @property
    def task_id(self) -> str: ...

    @property
    def action(self) -> str: ...

    @property
    def state(self) -> str: ...

    @property
    def checkpoint(self) -> int | None: ...


class TaskService(Protocol):
    """Domain operations required by GraphQL resolvers."""

    def submit_task(self, action: str, checkpoint: int | None = None) -> TaskRecord: ...

    def get_task(self, task_id: str) -> TaskRecord | None: ...

    def list_tasks(self, *, first: int) -> Sequence[TaskRecord]: ...


_SCHEMA = """
enum TaskState {
  QUEUED
  SCHEDULED
  RUNNING
  SUCCEEDED
  FAILED
  CANCELLED
}

type Task {
  id: ID!
  action: String!
  state: TaskState!
  checkpoint: Int
}

type Query {
  task(id: ID!): Task
  tasks(first: Int! = 20): [Task!]!
}

type Mutation {
  submitTask(action: String!, checkpoint: Int): Task!
}
"""


class SigRaftGraphQL:
    """Typed GraphQL schema whose resolvers call the SigRaft domain service."""

    def __init__(self, service: TaskService) -> None:
        self._service = service
        self.schema = build_schema(_SCHEMA)
        query = self.schema.get_type("Query")
        mutation = self.schema.get_type("Mutation")
        task = self.schema.get_type("Task")
        if query is None or mutation is None or task is None:
            raise RuntimeError("SigRaft GraphQL schema is incomplete")
        cast(Any, query).fields["task"].resolve = self._resolve_task
        cast(Any, query).fields["tasks"].resolve = self._resolve_tasks
        cast(Any, mutation).fields["submitTask"].resolve = self._resolve_submit
        cast(Any, task).fields["id"].resolve = lambda record, _info: record.task_id
        cast(Any, task).fields["state"].resolve = lambda record, _info: record.state.upper()

    def execute(
        self,
        document: str,
        *,
        variables: Mapping[str, object] | None = None,
        operation_name: str | None = None,
        scopes: frozenset[str] = frozenset(),
    ) -> ExecutionResult:
        """Validate and execute one query or mutation."""

        return graphql_sync(
            self.schema,
            document,
            variable_values=dict(variables) if variables is not None else None,
            operation_name=operation_name,
            context_value={"scopes": scopes},
        )

    def _resolve_task(
        self,
        _root: object,
        info: GraphQLResolveInfo,
        *,
        id: str,
    ) -> TaskRecord | None:
        _require_scope(info, "tasks:read")
        return self._service.get_task(id)

    def _resolve_tasks(
        self,
        _root: object,
        info: GraphQLResolveInfo,
        *,
        first: int,
    ) -> Sequence[TaskRecord]:
        _require_scope(info, "tasks:read")
        if first < 1 or first > 100:
            raise GraphQLError("first must be between 1 and 100")
        return self._service.list_tasks(first=first)

    def _resolve_submit(
        self,
        _root: object,
        info: GraphQLResolveInfo,
        *,
        action: str,
        checkpoint: int | None = None,
    ) -> TaskRecord:
        _require_scope(info, "tasks:write")
        try:
            return self._service.submit_task(action, checkpoint)
        except ValueError as exc:
            raise GraphQLError(str(exc), extensions={"code": "INVALID_TASK"}) from exc


def execute_http_payload(
    api: SigRaftGraphQL,
    payload: Mapping[str, object],
    *,
    scopes: frozenset[str],
) -> dict[str, object]:
    """Validate one GraphQL-over-HTTP JSON request and format its result."""

    query = payload.get("query")
    variables = payload.get("variables")
    operation_name = payload.get("operationName")
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be non-empty text")
    if variables is not None and not isinstance(variables, dict):
        raise ValueError("variables must be an object")
    if operation_name is not None and not isinstance(operation_name, str):
        raise ValueError("operationName must be text")
    result = api.execute(
        query,
        variables=cast(Mapping[str, object] | None, variables),
        operation_name=operation_name,
        scopes=scopes,
    )
    return cast(dict[str, object], result.formatted)


def _require_scope(info: GraphQLResolveInfo, required: str) -> None:
    context = cast(dict[str, frozenset[str]], info.context)
    if required not in context["scopes"]:
        raise GraphQLError(
            f"missing required scope: {required}",
            extensions={"code": "FORBIDDEN"},
        )
