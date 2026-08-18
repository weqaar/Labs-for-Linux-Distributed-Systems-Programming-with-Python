"""Checkpoint 06 for relay: Azure blob and queue adapters for `/tasks`."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from azure.core.credentials import TokenCredential
from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ResourceExistsError,
    ResourceNotFoundError,
    ServiceRequestError,
    ServiceResponseError,
)
from azure.identity import DefaultAzureCredential
from azure.storage.blob import BlobServiceClient, ContainerClient
from azure.storage.queue import QueueClient, QueueServiceClient

TASK_ID_PATTERN = re.compile(r"task-\d+\Z")


class TaskState(str, Enum):
    """States shared by the relay API and Azure adapters."""

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class RelayTaskSnapshot:
    """A task document stored in relay blobs and queue messages."""

    task_id: str
    action: str
    state: TaskState

    def __post_init__(self) -> None:
        if not TASK_ID_PATTERN.fullmatch(self.task_id):
            raise ValueError("task_id must look like task-17")
        if not self.action.strip():
            raise ValueError("task action must not be empty")

    @property
    def resource_path(self) -> str:
        return f"/tasks/{self.task_id}"

    def to_payload(self) -> dict[str, str]:
        return {
            "taskId": self.task_id,
            "action": self.action,
            "state": self.state.value,
            "resourcePath": self.resource_path,
        }

    def to_json_bytes(self) -> bytes:
        return json.dumps(self.to_payload(), sort_keys=True).encode("utf-8")

    def to_json_text(self) -> str:
        return self.to_json_bytes().decode("utf-8")

    @classmethod
    def from_json_bytes(cls, data: bytes) -> RelayTaskSnapshot:
        parsed: object = json.loads(data.decode("utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("task document must be a JSON object")
        task_id = parsed.get("taskId")
        action = parsed.get("action")
        state = parsed.get("state")
        resource_path = parsed.get("resourcePath")
        if not isinstance(task_id, str):
            raise ValueError("taskId must be a string")
        if not isinstance(action, str):
            raise ValueError("action must be a string")
        if not isinstance(state, str):
            raise ValueError("state must be a string")
        expected_resource_path = f"/tasks/{task_id}"
        if resource_path != expected_resource_path:
            raise ValueError("resourcePath must match taskId")
        return cls(task_id=task_id, action=action, state=TaskState(state))


@dataclass(frozen=True)
class AzureRetrySettings:
    """Azure client retry and timeout settings for relay data-plane code."""

    retry_total: int = 5
    retry_backoff_factor: float = 0.8
    connection_timeout: float = 5.0
    read_timeout: float = 30.0


DEFAULT_RETRY_SETTINGS = AzureRetrySettings()


class RelayAzureError(Exception):
    """Base class for relay Azure adapter failures."""


class RelayAuthenticationError(RelayAzureError):
    """Raised when no usable Azure credential exists."""


class RelayPermissionError(RelayAzureError):
    """Raised when Azure denied the data-plane operation."""


class RelayTaskNotFoundError(RelayAzureError):
    """Raised when relay cannot find the requested task document."""


class RelayOperationError(RelayAzureError):
    """Raised for Azure failures that the caller may need to retry."""

    def __init__(self, operation: str, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.operation = operation
        self.retryable = retryable


class BlobDownloadStreamProtocol(Protocol):
    """Subset of the blob downloader used by relay tests."""

    def readall(self) -> bytes:
        """Return the blob body."""
        ...


class BlobItemProtocol(Protocol):
    """Subset of blob list items used by relay tests."""

    @property
    def name(self) -> str:
        """Return the blob name."""
        ...


class BlobPagerProtocol(Protocol):
    """Subset of Azure paging used by the relay blob adapter."""

    def by_page(
        self,
        continuation_token: str | None = None,
        results_per_page: int | None = None,
    ) -> Iterator[Iterable[BlobItemProtocol]]:
        """Yield pages of blob items."""
        ...


class BlobContainerClientProtocol(Protocol):
    """Subset of the blob container client used by relay."""

    def create_container(self) -> object:
        """Create the container."""
        ...

    def upload_blob(self, name: str, data: bytes, overwrite: bool) -> object:
        """Upload one task document."""
        ...

    def download_blob(self, blob: str) -> BlobDownloadStreamProtocol:
        """Download one task document."""
        ...

    def list_blobs(self, name_starts_with: str | None = None) -> BlobPagerProtocol:
        """List task document blobs."""
        ...


class QueueClientProtocol(Protocol):
    """Subset of the queue client used by relay."""

    def create_queue(self) -> object:
        """Create the queue."""
        ...

    def send_message(self, content: str) -> object:
        """Enqueue one relay task message."""
        ...


class BlobServiceClientProtocol(Protocol):
    """Subset of the blob service client used by relay."""

    def get_container_client(self, container: str) -> BlobContainerClientProtocol:
        """Return the blob container client."""
        ...


class QueueServiceClientProtocol(Protocol):
    """Subset of the queue service client used by relay."""

    def get_queue_client(self, queue: str) -> QueueClientProtocol:
        """Return the queue client."""
        ...


class BlobServiceFactory(Protocol):
    """Factory boundary for building blob service clients."""

    def __call__(
        self,
        *,
        account_url: str,
        credential: TokenCredential,
        retry_settings: AzureRetrySettings,
    ) -> BlobServiceClientProtocol:
        """Create a blob service client."""
        ...


class QueueServiceFactory(Protocol):
    """Factory boundary for building queue service clients."""

    def __call__(
        self,
        *,
        account_url: str,
        credential: TokenCredential,
        retry_settings: AzureRetrySettings,
    ) -> QueueServiceClientProtocol:
        """Create a queue service client."""
        ...


class RelayTaskStore(Protocol):
    """Typed boundary used by relay code that persists task documents."""

    def ensure_container(self) -> bool:
        """Create the container once and treat existing state as success."""
        ...

    def put_task(self, task: RelayTaskSnapshot) -> None:
        """Persist one task document."""
        ...

    def get_task(self, task_id: str) -> RelayTaskSnapshot:
        """Load one task document."""
        ...

    def iter_task_pages(self, *, page_size: int = 100) -> Iterator[tuple[RelayTaskSnapshot, ...]]:
        """Iterate task documents page by page."""
        ...


class RelayTaskQueue(Protocol):
    """Typed boundary used by relay code that dispatches work."""

    def ensure_queue(self) -> bool:
        """Create the queue once and treat existing state as success."""
        ...

    def enqueue_task(self, task: RelayTaskSnapshot) -> None:
        """Enqueue one relay task message."""
        ...


class AzureBlobTaskStore:
    """Persist relay task documents in Azure Blob Storage."""

    def __init__(self, container_client: BlobContainerClientProtocol) -> None:
        self._container_client = container_client

    def ensure_container(self) -> bool:
        try:
            self._container_client.create_container()
        except ResourceExistsError:
            return False
        except ClientAuthenticationError as error:
            raise RelayAuthenticationError(
                _message_for("create relay task container", error)
            ) from error
        except ServiceRequestError as error:
            raise RelayOperationError(
                "create relay task container",
                _message_for("create relay task container", error),
                retryable=True,
            ) from error
        except ServiceResponseError as error:
            raise RelayOperationError(
                "create relay task container",
                _message_for("create relay task container", error),
                retryable=True,
            ) from error
        except HttpResponseError as error:
            raise _http_error("create relay task container", error) from error
        return True

    def put_task(self, task: RelayTaskSnapshot) -> None:
        try:
            self._container_client.upload_blob(
                name=_blob_name(task.task_id),
                data=task.to_json_bytes(),
                overwrite=True,
            )
        except ClientAuthenticationError as error:
            raise RelayAuthenticationError(
                _message_for("write relay task document", error)
            ) from error
        except ServiceRequestError as error:
            raise RelayOperationError(
                "write relay task document",
                _message_for("write relay task document", error),
                retryable=True,
            ) from error
        except ServiceResponseError as error:
            raise RelayOperationError(
                "write relay task document",
                _message_for("write relay task document", error),
                retryable=True,
            ) from error
        except HttpResponseError as error:
            raise _http_error("write relay task document", error) from error

    def get_task(self, task_id: str) -> RelayTaskSnapshot:
        try:
            downloader = self._container_client.download_blob(blob=_blob_name(task_id))
            payload = downloader.readall()
        except ClientAuthenticationError as error:
            raise RelayAuthenticationError(
                _message_for("read relay task document", error)
            ) from error
        except ResourceNotFoundError as error:
            raise RelayTaskNotFoundError(f"task {task_id} does not exist") from error
        except ServiceRequestError as error:
            raise RelayOperationError(
                "read relay task document",
                _message_for("read relay task document", error),
                retryable=True,
            ) from error
        except ServiceResponseError as error:
            raise RelayOperationError(
                "read relay task document",
                _message_for("read relay task document", error),
                retryable=True,
            ) from error
        except HttpResponseError as error:
            raise _http_error("read relay task document", error) from error
        return RelayTaskSnapshot.from_json_bytes(payload)

    def iter_task_pages(
        self,
        *,
        page_size: int = 100,
    ) -> Iterator[tuple[RelayTaskSnapshot, ...]]:
        try:
            pager = self._container_client.list_blobs(name_starts_with="task-")
        except ClientAuthenticationError as error:
            raise RelayAuthenticationError(
                _message_for("list relay task documents", error)
            ) from error
        except ServiceRequestError as error:
            raise RelayOperationError(
                "list relay task documents",
                _message_for("list relay task documents", error),
                retryable=True,
            ) from error
        except ServiceResponseError as error:
            raise RelayOperationError(
                "list relay task documents",
                _message_for("list relay task documents", error),
                retryable=True,
            ) from error
        except HttpResponseError as error:
            raise _http_error("list relay task documents", error) from error

        for page in pager.by_page(results_per_page=page_size):
            snapshots: list[RelayTaskSnapshot] = []
            for item in page:
                task_id = item.name.removesuffix(".json")
                snapshots.append(self.get_task(task_id))
            yield tuple(snapshots)


class AzureQueueTaskDispatcher:
    """Enqueue relay work in Azure Queue Storage."""

    def __init__(self, queue_client: QueueClientProtocol) -> None:
        self._queue_client = queue_client

    def ensure_queue(self) -> bool:
        try:
            self._queue_client.create_queue()
        except ResourceExistsError:
            return False
        except ClientAuthenticationError as error:
            raise RelayAuthenticationError(
                _message_for("create relay tasks queue", error)
            ) from error
        except ServiceRequestError as error:
            raise RelayOperationError(
                "create relay tasks queue",
                _message_for("create relay tasks queue", error),
                retryable=True,
            ) from error
        except ServiceResponseError as error:
            raise RelayOperationError(
                "create relay tasks queue",
                _message_for("create relay tasks queue", error),
                retryable=True,
            ) from error
        except HttpResponseError as error:
            raise _http_error("create relay tasks queue", error) from error
        return True

    def enqueue_task(self, task: RelayTaskSnapshot) -> None:
        try:
            self._queue_client.send_message(task.to_json_text())
        except ClientAuthenticationError as error:
            raise RelayAuthenticationError(_message_for("enqueue relay task", error)) from error
        except ServiceRequestError as error:
            raise RelayOperationError(
                "enqueue relay task",
                _message_for("enqueue relay task", error),
                retryable=True,
            ) from error
        except ServiceResponseError as error:
            raise RelayOperationError(
                "enqueue relay task",
                _message_for("enqueue relay task", error),
                retryable=False,
            ) from error
        except HttpResponseError as error:
            raise _http_error("enqueue relay task", error) from error


@dataclass(frozen=True)
class BlobItemAdapter:
    """Expose just the blob name through the relay protocol boundary."""

    name: str


@dataclass(frozen=True)
class BlobPagerAdapter:
    """Adapt Azure paging to the relay paging protocol."""

    client: ContainerClient
    name_starts_with: str | None = None

    def by_page(
        self,
        continuation_token: str | None = None,
        results_per_page: int | None = None,
    ) -> Iterator[Iterable[BlobItemProtocol]]:
        pager = self.client.list_blobs(
            name_starts_with=self.name_starts_with,
            results_per_page=results_per_page,
        )
        for page in pager.by_page(continuation_token=continuation_token):
            yield tuple(BlobItemAdapter(item.name) for item in page)


@dataclass(frozen=True)
class BlobContainerClientAdapter:
    """Adapt the Azure SDK container client to the relay protocol boundary."""

    client: ContainerClient

    def create_container(self) -> object:
        return self.client.create_container()

    def upload_blob(self, name: str, data: bytes, overwrite: bool) -> object:
        return self.client.upload_blob(name, data, overwrite=overwrite)

    def download_blob(self, blob: str) -> BlobDownloadStreamProtocol:
        return self.client.download_blob(blob)

    def list_blobs(self, name_starts_with: str | None = None) -> BlobPagerProtocol:
        return BlobPagerAdapter(self.client, name_starts_with)


@dataclass(frozen=True)
class QueueClientAdapter:
    """Adapt the Azure SDK queue client to the relay protocol boundary."""

    client: QueueClient

    def create_queue(self) -> object:
        return self.client.create_queue()

    def send_message(self, content: str) -> object:
        return self.client.send_message(content)


@dataclass(frozen=True)
class BlobServiceClientAdapter:
    """Adapt the Azure SDK blob service client to the relay protocol boundary."""

    client: BlobServiceClient

    def get_container_client(self, container: str) -> BlobContainerClientProtocol:
        return BlobContainerClientAdapter(self.client.get_container_client(container))


@dataclass(frozen=True)
class QueueServiceClientAdapter:
    """Adapt the Azure SDK queue service client to the relay protocol boundary."""

    client: QueueServiceClient

    def get_queue_client(self, queue: str) -> QueueClientProtocol:
        return QueueClientAdapter(self.client.get_queue_client(queue))


def build_blob_task_store(
    *,
    account_url: str,
    container_name: str = "tasks",
    credential: TokenCredential | None = None,
    retry_settings: AzureRetrySettings = DEFAULT_RETRY_SETTINGS,
    service_factory: BlobServiceFactory | None = None,
) -> AzureBlobTaskStore:
    """Build the relay blob store with shared credential and retry settings."""

    active_credential = credential if credential is not None else DefaultAzureCredential()
    active_factory = default_blob_service_factory if service_factory is None else service_factory
    service_client = active_factory(
        account_url=account_url,
        credential=active_credential,
        retry_settings=retry_settings,
    )
    return AzureBlobTaskStore(service_client.get_container_client(container_name))


def build_queue_task_dispatcher(
    *,
    account_url: str,
    queue_name: str = "tasks",
    credential: TokenCredential | None = None,
    retry_settings: AzureRetrySettings = DEFAULT_RETRY_SETTINGS,
    service_factory: QueueServiceFactory | None = None,
) -> AzureQueueTaskDispatcher:
    """Build the relay queue dispatcher with shared credential and retry settings."""

    active_credential = credential if credential is not None else DefaultAzureCredential()
    active_factory = default_queue_service_factory if service_factory is None else service_factory
    service_client = active_factory(
        account_url=account_url,
        credential=active_credential,
        retry_settings=retry_settings,
    )
    return AzureQueueTaskDispatcher(service_client.get_queue_client(queue_name))


def default_blob_service_factory(
    *,
    account_url: str,
    credential: TokenCredential,
    retry_settings: AzureRetrySettings,
) -> BlobServiceClientProtocol:
    """Create the real Azure blob service client for relay."""

    return BlobServiceClientAdapter(
        BlobServiceClient(
            account_url=account_url,
            credential=credential,
            retry_total=retry_settings.retry_total,
            retry_backoff_factor=retry_settings.retry_backoff_factor,
            connection_timeout=retry_settings.connection_timeout,
            read_timeout=retry_settings.read_timeout,
        )
    )


def default_queue_service_factory(
    *,
    account_url: str,
    credential: TokenCredential,
    retry_settings: AzureRetrySettings,
) -> QueueServiceClientProtocol:
    """Create the real Azure queue service client for relay."""

    return QueueServiceClientAdapter(
        QueueServiceClient(
            account_url=account_url,
            credential=credential,
            retry_total=retry_settings.retry_total,
            retry_backoff_factor=retry_settings.retry_backoff_factor,
            connection_timeout=retry_settings.connection_timeout,
            read_timeout=retry_settings.read_timeout,
        )
    )


def _blob_name(task_id: str) -> str:
    return f"{task_id}.json"


def _http_error(operation: str, error: HttpResponseError) -> RelayAzureError:
    if error.status_code == 403:
        return RelayPermissionError(_message_for(operation, error))
    return RelayOperationError(operation, _message_for(operation, error), retryable=False)


def _message_for(operation: str, error: Exception) -> str:
    detail = str(error) or error.__class__.__name__
    return f"{operation} failed: {detail}"
