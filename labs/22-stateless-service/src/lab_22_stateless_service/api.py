"""FastAPI application for the relay stateless service checkpoint."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from threading import Lock
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from lab_22_stateless_service.contract import (
    CreateTaskCommand,
    PatchTaskCommand,
    RelayTask,
    TaskKey,
    TaskStatus,
)
from lab_22_stateless_service.repository import (
    ConcurrencyConflictError,
    InMemoryTaskRepository,
    RepositoryUnavailableError,
    TaskAlreadyExistsError,
    TaskNotFoundError,
    TaskRepository,
)


@dataclass(frozen=True)
class DrainSnapshot:
    """Observable drain state for health checks and tests."""

    draining: bool
    in_flight_requests: int


class ServiceState:
    """Tracks graceful drain state for one app instance."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._draining = False
        self._in_flight_requests = 0

    @property
    def is_draining(self) -> bool:
        with self._lock:
            return self._draining

    def begin_drain(self) -> DrainSnapshot:
        with self._lock:
            self._draining = True
            return DrainSnapshot(
                draining=self._draining,
                in_flight_requests=self._in_flight_requests,
            )

    def request_started(self) -> None:
        with self._lock:
            self._in_flight_requests += 1

    def request_finished(self) -> None:
        with self._lock:
            self._in_flight_requests -= 1

    def snapshot(self) -> DrainSnapshot:
        with self._lock:
            return DrainSnapshot(
                draining=self._draining,
                in_flight_requests=self._in_flight_requests,
            )


class TaskCreateRequest(BaseModel):
    """JSON body for POST /tasks."""

    tenant_id: str
    task_id: str
    title: str
    status: TaskStatus = TaskStatus.QUEUED
    payload: dict[str, str] = Field(default_factory=dict)
    depends_on: list[str] = Field(default_factory=list)


class TaskPatchRequest(BaseModel):
    """JSON body for PATCH /tasks."""

    title: str | None = None
    status: TaskStatus | None = None
    payload: dict[str, str] | None = None
    depends_on: list[str] | None = None


class TaskView(BaseModel):
    """Response model used by the HTTP API."""

    tenant_id: str
    task_id: str
    title: str
    status: TaskStatus
    payload: dict[str, str]
    depends_on: list[str]
    etag: str

    @classmethod
    def from_task(cls, task: RelayTask) -> TaskView:
        return cls(
            tenant_id=task.key.tenant_id,
            task_id=task.key.task_id,
            title=task.title,
            status=task.status,
            payload=dict(task.payload),
            depends_on=list(task.depends_on),
            etag=task.etag,
        )


def create_app(
    repository: TaskRepository | None = None,
    service_state: ServiceState | None = None,
) -> FastAPI:
    """Create an app instance bound to an injected repository."""

    repository = InMemoryTaskRepository() if repository is None else repository
    service_state = ServiceState() if service_state is None else service_state

    app = FastAPI(title="relay-task-service", version="0.1.0")
    app.state.repository = repository
    app.state.service_state = service_state

    health_paths = frozenset({"/livez", "/readyz", "/drain"})

    @app.middleware("http")
    async def track_requests(request, call_next):  # type: ignore[no-untyped-def]
        if service_state.is_draining and request.url.path not in health_paths:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"detail": "instance draining"},
            )
        service_state.request_started()
        try:
            return await call_next(request)
        finally:
            service_state.request_finished()

    @app.get("/livez")
    def livez() -> dict[str, object]:
        return {"status": "live", **asdict(service_state.snapshot())}

    @app.get("/readyz")
    def readyz() -> dict[str, object]:
        if service_state.is_draining:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="instance draining",
            )
        if not repository.is_ready():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="repository unavailable",
            )
        return {"status": "ready", **asdict(service_state.snapshot())}

    @app.post("/drain")
    def drain() -> dict[str, object]:
        return {"status": "draining", **asdict(service_state.begin_drain())}

    @app.post("/tasks", response_model=TaskView, status_code=status.HTTP_201_CREATED)
    def create_task(body: TaskCreateRequest, response: Response) -> TaskView:
        task = _translate_errors(
            lambda: repository.create_task(
                CreateTaskCommand(
                    key=TaskKey(tenant_id=body.tenant_id, task_id=body.task_id),
                    title=body.title,
                    status=body.status,
                    payload=body.payload,
                    depends_on=tuple(body.depends_on),
                )
            )
        )
        response.headers["ETag"] = _quote_etag(task.etag)
        return TaskView.from_task(task)

    @app.get("/tasks", response_model=list[TaskView])
    def list_tasks(
        tenant_id: Annotated[str | None, Query()] = None,
    ) -> list[TaskView]:
        tasks = _translate_errors(lambda: repository.list_tasks(tenant_id=tenant_id))
        return [TaskView.from_task(task) for task in tasks]

    @app.get("/tasks/{tenant_id}/{task_id}", response_model=TaskView)
    def get_task(tenant_id: str, task_id: str, response: Response) -> TaskView:
        task = _translate_errors(
            lambda: repository.get_task(TaskKey(tenant_id=tenant_id, task_id=task_id))
        )
        response.headers["ETag"] = _quote_etag(task.etag)
        return TaskView.from_task(task)

    @app.patch("/tasks/{tenant_id}/{task_id}", response_model=TaskView)
    def patch_task(
        tenant_id: str,
        task_id: str,
        body: TaskPatchRequest,
        response: Response,
        if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    ) -> TaskView:
        if if_match is None:
            raise HTTPException(
                status_code=status.HTTP_428_PRECONDITION_REQUIRED,
                detail="If-Match header required",
            )
        task = _translate_errors(
            lambda: repository.update_task(
                TaskKey(tenant_id=tenant_id, task_id=task_id),
                PatchTaskCommand(
                    title=body.title,
                    status=body.status,
                    payload=body.payload,
                    depends_on=tuple(body.depends_on) if body.depends_on is not None else None,
                ),
                if_match=_normalise_etag(if_match),
            )
        )
        response.headers["ETag"] = _quote_etag(task.etag)
        return TaskView.from_task(task)

    return app


def _translate_errors(operation):  # type: ignore[no-untyped-def]
    try:
        return operation()
    except TaskAlreadyExistsError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except TaskNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ConcurrencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_412_PRECONDITION_FAILED,
            detail=str(exc),
        ) from exc
    except RepositoryUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc


def _quote_etag(etag: str) -> str:
    return f'"{etag}"'


def _normalise_etag(value: str) -> str:
    stripped = value.strip()
    if stripped.startswith("W/"):
        stripped = stripped[2:].strip()
    if stripped.startswith('"') and stripped.endswith('"'):
        stripped = stripped[1:-1]
    return stripped
