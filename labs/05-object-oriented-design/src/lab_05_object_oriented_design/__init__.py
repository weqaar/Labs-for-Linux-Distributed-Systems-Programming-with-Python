"""Object-oriented relay service checkpoint."""

from __future__ import annotations

from .handlers import ArchiveHandler, Handler, HandlerMeta, IndexHandler
from .models import Task, TaskAction, TaskState, WorkerPolicy
from .repository import InMemoryTaskRepository, TaskRepository
from .service import AuditEvent, TaskService, build_default_service

__version__ = "0.1.0"

__all__ = [
    "ArchiveHandler",
    "AuditEvent",
    "Handler",
    "HandlerMeta",
    "InMemoryTaskRepository",
    "IndexHandler",
    "Task",
    "TaskAction",
    "TaskRepository",
    "TaskService",
    "TaskState",
    "WorkerPolicy",
    "__version__",
    "build_default_service",
]
