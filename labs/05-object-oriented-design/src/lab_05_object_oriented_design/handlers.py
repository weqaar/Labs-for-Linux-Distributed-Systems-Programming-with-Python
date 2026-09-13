"""Polymorphic relay task handlers and their registry."""

from __future__ import annotations

from abc import ABC, abstractmethod
from types import MappingProxyType
from typing import ClassVar, cast

from .models import Task, TaskAction


class HandlerMeta(type(ABC)):
    """Register concrete handlers by action at class creation time."""

    _registry: dict[TaskAction, type[Handler]] = {}

    def __new__(
        mcls,
        name: str,
        bases: tuple[type[object], ...],
        namespace: dict[str, object],
    ) -> HandlerMeta:
        created = cast("type[Handler]", super().__new__(mcls, name, bases, namespace))
        action = namespace.get("action")
        if isinstance(action, TaskAction):
            mcls._registry[action] = created
        return created

    @classmethod
    def registry(mcls) -> MappingProxyType[TaskAction, type[Handler]]:
        """Expose a read-only view of registered handler classes."""

        return MappingProxyType(mcls._registry)


class Handler(ABC, metaclass=HandlerMeta):
    """Nominal base where shared lifecycle behavior is required."""

    action: ClassVar[TaskAction]

    @abstractmethod
    def execute(self, task: Task) -> str:
        """Execute one task and return an operator-facing result."""


class PrefixMixin:
    """Cooperative mixin adding a stable result prefix."""

    def prefix(self) -> str:
        return "relay"


class IndexHandler(PrefixMixin, Handler):
    """Index task handler."""

    action = TaskAction.INDEX

    def execute(self, task: Task) -> str:
        return f"{self.prefix()}: indexed {task.payload}"


class ArchiveHandler(PrefixMixin, Handler):
    """Archive task handler."""

    action = TaskAction.ARCHIVE

    def execute(self, task: Task) -> str:
        return f"{self.prefix()}: archived {task.payload}"
