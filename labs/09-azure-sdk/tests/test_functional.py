"""Functional tests for checkpoint 09.

These tests drive the composed adapters returned by the public builders
`build_blob_task_store` and `build_queue_task_dispatcher`. Each test submits a
job the way relay would: write its task document to blob storage, enqueue it,
move it through `queued`, `running` and `succeeded` or `failed`, and read the
result back. Deterministic fake SDK clients stand in for Azure, so no
subscription, credential or network access is needed.
"""

# pyright: strict

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

import pytest
from azure.core.credentials import AccessToken, TokenCredential
from azure.core.exceptions import (
    HttpResponseError,
    ResourceExistsError,
    ResourceNotFoundError,
    ServiceResponseError,
)

from lab_09_azure_sdk import (
    AzureBlobTaskStore,
    AzureQueueTaskDispatcher,
    AzureRetrySettings,
    RelayOperationError,
    RelayPermissionError,
    RelayTaskNotFoundError,
    RelayTaskSnapshot,
    TaskState,
    build_blob_task_store,
    build_queue_task_dispatcher,
)

ACCOUNT = "https://relaytasks.blob.core.windows.net"
QUEUE_ACCOUNT = "https://relaytasks.queue.core.windows.net"


@dataclass(frozen=True)
class FakeBlobItem:
    name: str


class FakeDownload:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def readall(self) -> bytes:
        return self._payload


@dataclass
class FakePager:
    names: tuple[str, ...]

    def by_page(
        self,
        continuation_token: str | None = None,
        results_per_page: int | None = None,
    ) -> Iterator[Iterable[FakeBlobItem]]:
        del continuation_token
        size = results_per_page or max(len(self.names), 1)
        for start in range(0, len(self.names), size):
            yield tuple(FakeBlobItem(name) for name in self.names[start : start + size])


@dataclass
class FakeContainer:
    """In-memory blob container that behaves like the Azure SDK subset relay uses."""

    exists: bool = False
    blobs: dict[str, bytes] = field(default_factory=dict[str, bytes])
    deny_reads: bool = False

    def create_container(self) -> object:
        if self.exists:
            raise ResourceExistsError("container exists")
        self.exists = True
        return {}

    def upload_blob(self, name: str, data: bytes, overwrite: bool) -> object:
        if not overwrite and name in self.blobs:
            raise ResourceExistsError("blob exists")
        self.blobs[name] = data
        return {}

    def download_blob(self, blob: str) -> FakeDownload:
        if self.deny_reads:
            error = HttpResponseError(message="this request is not authorized")
            error.status_code = 403
            raise error
        if blob not in self.blobs:
            raise ResourceNotFoundError("blob not found")
        return FakeDownload(self.blobs[blob])

    def list_blobs(self, name_starts_with: str | None = None) -> FakePager:
        prefix = name_starts_with or ""
        return FakePager(tuple(sorted(name for name in self.blobs if name.startswith(prefix))))


@dataclass
class FakeQueue:
    """In-memory queue that can lose the reply after the message was stored."""

    exists: bool = False
    messages: list[str] = field(default_factory=list[str])
    lose_next_reply: bool = False

    def create_queue(self) -> object:
        if self.exists:
            raise ResourceExistsError("queue exists")
        self.exists = True
        return {}

    def send_message(self, content: str) -> object:
        self.messages.append(content)
        if self.lose_next_reply:
            self.lose_next_reply = False
            raise ServiceResponseError("connection reset after the request was sent")
        return {}


@dataclass
class FakeBlobService:
    container: FakeContainer

    def get_container_client(self, container: str) -> FakeContainer:
        if container != "tasks":
            raise ValueError(f"unexpected container {container}")
        return self.container


@dataclass
class FakeQueueService:
    queue: FakeQueue

    def get_queue_client(self, queue: str) -> FakeQueue:
        if queue != "tasks":
            raise ValueError(f"unexpected queue {queue}")
        return self.queue


class FixedCredential:
    def get_token(
        self,
        *scopes: str,
        claims: str | None = None,
        tenant_id: str | None = None,
        enable_cae: bool = False,
        **kwargs: object,
    ) -> AccessToken:
        del scopes, claims, tenant_id, enable_cae, kwargs
        return AccessToken("fake-token", 2_000_000_000)


@dataclass
class Relay:
    store: AzureBlobTaskStore
    dispatcher: AzureQueueTaskDispatcher
    container: FakeContainer
    queue: FakeQueue


@pytest.fixture
def relay() -> Relay:
    container = FakeContainer()
    queue = FakeQueue()

    def blob_factory(
        *, account_url: str, credential: TokenCredential, retry_settings: AzureRetrySettings
    ) -> FakeBlobService:
        del credential, retry_settings
        if account_url != ACCOUNT:
            raise ValueError(f"unexpected account {account_url}")
        return FakeBlobService(container)

    def queue_factory(
        *, account_url: str, credential: TokenCredential, retry_settings: AzureRetrySettings
    ) -> FakeQueueService:
        del credential, retry_settings
        if account_url != QUEUE_ACCOUNT:
            raise ValueError(f"unexpected account {account_url}")
        return FakeQueueService(queue)

    credential = FixedCredential()
    store = build_blob_task_store(
        account_url=ACCOUNT, credential=credential, service_factory=blob_factory
    )
    dispatcher = build_queue_task_dispatcher(
        account_url=QUEUE_ACCOUNT, credential=credential, service_factory=queue_factory
    )
    return Relay(store, dispatcher, container, queue)


def submit(relay: Relay, task_id: str, action: str) -> RelayTaskSnapshot:
    task = RelayTaskSnapshot(task_id, action, TaskState.QUEUED)
    relay.store.put_task(task)
    relay.dispatcher.enqueue_task(task)
    return task


def test_submitted_job_moves_through_running_to_succeeded_in_blob_storage(
    relay: Relay,
) -> None:
    assert relay.store.ensure_container() is True
    assert relay.dispatcher.ensure_queue() is True
    assert relay.store.ensure_container() is False
    assert relay.dispatcher.ensure_queue() is False

    submitted = submit(relay, "task-17", "rebuild-search-index")
    message = RelayTaskSnapshot.from_json_bytes(relay.queue.messages[0].encode("utf-8"))
    assert message == submitted
    assert relay.store.get_task("task-17").state is TaskState.QUEUED

    for state in (TaskState.RUNNING, TaskState.SUCCEEDED):
        relay.store.put_task(RelayTaskSnapshot("task-17", "rebuild-search-index", state))
        assert relay.store.get_task("task-17").state is state

    finished = relay.store.get_task("task-17")
    assert finished.resource_path == "/tasks/task-17"
    assert len(relay.queue.messages) == 1


def test_listing_returns_every_job_with_its_final_state_page_by_page(relay: Relay) -> None:
    relay.store.ensure_container()
    for number in (17, 18, 19):
        submit(relay, f"task-{number}", "compact-queue")
    relay.store.put_task(RelayTaskSnapshot("task-17", "compact-queue", TaskState.SUCCEEDED))
    relay.store.put_task(RelayTaskSnapshot("task-18", "compact-queue", TaskState.FAILED))

    pages = list(relay.store.iter_task_pages(page_size=2))

    assert [len(page) for page in pages] == [2, 1]
    states = {task.task_id: task.state for page in pages for task in page}
    assert states == {
        "task-17": TaskState.SUCCEEDED,
        "task-18": TaskState.FAILED,
        "task-19": TaskState.QUEUED,
    }


def test_lost_enqueue_reply_is_not_retryable_because_the_message_may_exist(
    relay: Relay,
) -> None:
    relay.queue.lose_next_reply = True
    task = RelayTaskSnapshot("task-17", "rebuild-search-index", TaskState.QUEUED)
    relay.store.put_task(task)

    with pytest.raises(RelayOperationError) as caught:
        relay.dispatcher.enqueue_task(task)

    assert caught.value.retryable is False
    assert caught.value.operation == "enqueue relay task"
    assert len(relay.queue.messages) == 1
    assert relay.store.get_task("task-17") == task


def test_unknown_job_and_denied_read_fail_with_distinct_errors(relay: Relay) -> None:
    with pytest.raises(RelayTaskNotFoundError, match="task-99"):
        relay.store.get_task("task-99")

    submit(relay, "task-17", "rebuild-search-index")
    relay.container.deny_reads = True

    with pytest.raises(RelayPermissionError, match="read relay task document"):
        relay.store.get_task("task-17")
