"""Tests for the relay Azure SDK checkpoint."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field

import pytest
from azure.core.credentials import AccessToken, TokenCredential
from azure.core.exceptions import (
    ClientAuthenticationError,
    HttpResponseError,
    ResourceExistsError,
    ResourceNotFoundError,
    ServiceRequestError,
    ServiceResponseError,
)

from lab_06_azure_sdk import (
    AzureBlobTaskStore,
    AzureQueueTaskDispatcher,
    AzureRetrySettings,
    RelayAuthenticationError,
    RelayOperationError,
    RelayPermissionError,
    RelayTaskNotFoundError,
    RelayTaskSnapshot,
    TaskState,
    __version__,
    build_blob_task_store,
    build_queue_task_dispatcher,
    default_blob_service_factory,
    default_queue_service_factory,
)


@dataclass(frozen=True)
class FakeBlobItem:
    name: str


class FakeBlobPager:
    def __init__(self, items: Sequence[FakeBlobItem]) -> None:
        self._items = tuple(items)
        self.requested_page_sizes: list[int | None] = []

    def by_page(
        self,
        continuation_token: str | None = None,
        results_per_page: int | None = None,
    ) -> Iterator[Iterable[FakeBlobItem]]:
        del continuation_token
        self.requested_page_sizes.append(results_per_page)
        page_size = results_per_page or max(len(self._items), 1)
        for index in range(0, len(self._items), page_size):
            yield self._items[index : index + page_size]


class FakeDownloadStream:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def readall(self) -> bytes:
        return self._payload


@dataclass
class FakeBlobContainerClient:
    blobs: dict[str, bytes] = field(default_factory=dict)
    create_side_effect: Exception | None = None
    upload_side_effect: Exception | None = None
    download_side_effects: dict[str, Exception] = field(default_factory=dict)
    pager: FakeBlobPager | None = None
    download_calls: list[str] = field(default_factory=list)

    def create_container(self) -> object:
        if self.create_side_effect is not None:
            raise self.create_side_effect
        return {"created": True}

    def upload_blob(self, name: str, data: bytes, overwrite: bool) -> object:
        del overwrite
        if self.upload_side_effect is not None:
            raise self.upload_side_effect
        self.blobs[name] = data
        return {"uploaded": name}

    def download_blob(self, blob: str) -> FakeDownloadStream:
        self.download_calls.append(blob)
        if blob in self.download_side_effects:
            raise self.download_side_effects[blob]
        try:
            payload = self.blobs[blob]
        except KeyError as error:
            raise ResourceNotFoundError("missing blob") from error
        return FakeDownloadStream(payload)

    def list_blobs(self, name_starts_with: str | None = None) -> FakeBlobPager:
        if self.pager is None:
            names = sorted(
                name
                for name in self.blobs
                if name_starts_with is None or name.startswith(name_starts_with)
            )
            self.pager = FakeBlobPager([FakeBlobItem(name) for name in names])
        return self.pager


@dataclass
class FakeQueueClient:
    create_side_effect: Exception | None = None
    send_side_effect: Exception | None = None
    messages: list[str] = field(default_factory=list)

    def create_queue(self) -> object:
        if self.create_side_effect is not None:
            raise self.create_side_effect
        return {"created": True}

    def send_message(self, content: str) -> object:
        if self.send_side_effect is not None:
            raise self.send_side_effect
        self.messages.append(content)
        return {"message": content}


@dataclass
class FakeBlobServiceClient:
    container_client: FakeBlobContainerClient

    def get_container_client(self, container: str) -> FakeBlobContainerClient:
        del container
        return self.container_client


@dataclass
class FakeQueueServiceClient:
    queue_client: FakeQueueClient

    def get_queue_client(self, queue: str) -> FakeQueueClient:
        del queue
        return self.queue_client


@dataclass
class FakeBlobServiceFactory:
    container_client: FakeBlobContainerClient
    account_url: str | None = None
    retry_settings: AzureRetrySettings | None = None
    credential: TokenCredential | None = None

    def __call__(
        self,
        *,
        account_url: str,
        credential: TokenCredential,
        retry_settings: AzureRetrySettings,
    ) -> FakeBlobServiceClient:
        self.account_url = account_url
        self.credential = credential
        self.retry_settings = retry_settings
        return FakeBlobServiceClient(self.container_client)


@dataclass
class FakeQueueServiceFactory:
    queue_client: FakeQueueClient
    account_url: str | None = None
    retry_settings: AzureRetrySettings | None = None
    credential: TokenCredential | None = None

    def __call__(
        self,
        *,
        account_url: str,
        credential: TokenCredential,
        retry_settings: AzureRetrySettings,
    ) -> FakeQueueServiceClient:
        self.account_url = account_url
        self.credential = credential
        self.retry_settings = retry_settings
        return FakeQueueServiceClient(self.queue_client)


class DummyCredential:
    def get_token(
        self,
        *scopes: str,
        claims: str | None = None,
        tenant_id: str | None = None,
        enable_cae: bool = False,
        **kwargs: object,
    ) -> AccessToken:
        del scopes, claims, tenant_id, enable_cae, kwargs
        return AccessToken("relay-token", 2_000_000_000)


class ForbiddenHttpResponseError(HttpResponseError):
    def __init__(self) -> None:
        super().__init__(message="forbidden")
        self.status_code = 403


class ConflictHttpResponseError(HttpResponseError):
    def __init__(self) -> None:
        super().__init__(message="conflict")
        self.status_code = 409


def make_task(task_id: str, state: TaskState = TaskState.QUEUED) -> RelayTaskSnapshot:
    return RelayTaskSnapshot(task_id=task_id, action="rebuild-search-index", state=state)


@pytest.mark.parametrize(
    ("task_id", "action", "message"),
    [
        ("17", "rebuild-search-index", "task_id must look like task-17"),
        ("task-17", "   ", "task action must not be empty"),
    ],
)
def test_task_snapshot_validation_rejects_bad_input(
    task_id: str,
    action: str,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        RelayTaskSnapshot(task_id=task_id, action=action, state=TaskState.QUEUED)


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        (b"[]", "task document must be a JSON object"),
        (
            b'{"taskId": 17, "action": "run", "state": "queued", "resourcePath": "/tasks/task-17"}',
            "taskId must be a string",
        ),
        (
            (
                b'{"taskId": "task-17", "action": 7, "state": "queued", '
                b'"resourcePath": "/tasks/task-17"}'
            ),
            "action must be a string",
        ),
        (
            b'{"taskId": "task-17", "action": "run", "state": 9, "resourcePath": "/tasks/task-17"}',
            "state must be a string",
        ),
        (
            (
                b'{"taskId": "task-17", "action": "run", "state": "queued", '
                b'"resourcePath": "/tasks/task-18"}'
            ),
            "resourcePath must match taskId",
        ),
    ],
)
def test_task_snapshot_parser_rejects_bad_documents(payload: bytes, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        RelayTaskSnapshot.from_json_bytes(payload)


def test_builders_pass_retry_settings_and_credential_to_factories() -> None:
    retry_settings = AzureRetrySettings(
        retry_total=7,
        retry_backoff_factor=1.2,
        connection_timeout=3.0,
        read_timeout=9.0,
    )
    credential = DummyCredential()
    blob_factory = FakeBlobServiceFactory(FakeBlobContainerClient())
    queue_factory = FakeQueueServiceFactory(FakeQueueClient())

    build_blob_task_store(
        account_url="https://relay.blob.core.windows.net",
        credential=credential,
        retry_settings=retry_settings,
        service_factory=blob_factory,
    )
    build_queue_task_dispatcher(
        account_url="https://relay.queue.core.windows.net",
        credential=credential,
        retry_settings=retry_settings,
        service_factory=queue_factory,
    )

    assert blob_factory.account_url == "https://relay.blob.core.windows.net"
    assert blob_factory.credential is credential
    assert blob_factory.retry_settings == retry_settings
    assert queue_factory.account_url == "https://relay.queue.core.windows.net"
    assert queue_factory.credential is credential
    assert queue_factory.retry_settings == retry_settings


def test_ensure_container_is_idempotent_when_it_already_exists() -> None:
    store = AzureBlobTaskStore(
        FakeBlobContainerClient(create_side_effect=ResourceExistsError("exists"))
    )

    assert store.ensure_container() is False


def test_ensure_container_returns_true_when_created() -> None:
    store = AzureBlobTaskStore(FakeBlobContainerClient())

    assert store.ensure_container() is True


def test_blob_store_round_trips_tasks_and_pages_lazily() -> None:
    container = FakeBlobContainerClient()
    store = AzureBlobTaskStore(container)
    for task_id in ("task-17", "task-18", "task-19"):
        store.put_task(make_task(task_id))

    pages = store.iter_task_pages(page_size=2)
    first_page = next(pages)
    second_page = next(pages)

    assert [task.task_id for task in first_page] == ["task-17", "task-18"]
    assert [task.task_id for task in second_page] == ["task-19"]
    assert container.pager is not None
    assert container.pager.requested_page_sizes == [2]
    assert container.download_calls == ["task-17.json", "task-18.json", "task-19.json"]


def test_get_task_reports_permission_problem_not_bad_credential() -> None:
    task = make_task("task-17")
    container = FakeBlobContainerClient(
        blobs={"task-17.json": task.to_json_bytes()},
        download_side_effects={"task-17.json": ForbiddenHttpResponseError()},
    )
    store = AzureBlobTaskStore(container)

    with pytest.raises(RelayPermissionError):
        store.get_task("task-17")


def test_get_task_reports_authentication_problem_separately() -> None:
    container = FakeBlobContainerClient(
        download_side_effects={"task-17.json": ClientAuthenticationError("bad credential")}
    )
    store = AzureBlobTaskStore(container)

    with pytest.raises(RelayAuthenticationError):
        store.get_task("task-17")


def test_get_task_reports_missing_task_explicitly() -> None:
    store = AzureBlobTaskStore(FakeBlobContainerClient())

    with pytest.raises(RelayTaskNotFoundError, match="task task-17 does not exist"):
        store.get_task("task-17")


def test_blob_write_marks_request_loss_retryable() -> None:
    store = AzureBlobTaskStore(
        FakeBlobContainerClient(upload_side_effect=ServiceRequestError("dns failure"))
    )

    with pytest.raises(RelayOperationError) as caught:
        store.put_task(make_task("task-17"))

    assert caught.value.retryable is True
    assert caught.value.operation == "write relay task document"


def test_blob_write_marks_response_loss_retryable_for_idempotent_overwrite() -> None:
    store = AzureBlobTaskStore(
        FakeBlobContainerClient(upload_side_effect=ServiceResponseError("reply lost"))
    )

    with pytest.raises(RelayOperationError) as caught:
        store.put_task(make_task("task-17"))

    assert caught.value.retryable is True
    assert caught.value.operation == "write relay task document"


def test_http_response_errors_become_non_retryable_operation_errors() -> None:
    store = AzureBlobTaskStore(
        FakeBlobContainerClient(upload_side_effect=ConflictHttpResponseError())
    )

    with pytest.raises(RelayOperationError) as caught:
        store.put_task(make_task("task-17"))

    assert caught.value.retryable is False


def test_ensure_queue_returns_true_when_created() -> None:
    dispatcher = AzureQueueTaskDispatcher(FakeQueueClient())

    assert dispatcher.ensure_queue() is True


def test_queue_send_marks_response_loss_not_retryable() -> None:
    dispatcher = AzureQueueTaskDispatcher(
        FakeQueueClient(send_side_effect=ServiceResponseError("reply lost"))
    )

    with pytest.raises(RelayOperationError) as caught:
        dispatcher.enqueue_task(make_task("task-17"))

    assert caught.value.retryable is False
    assert caught.value.operation == "enqueue relay task"


def test_queue_send_marks_request_loss_retryable() -> None:
    dispatcher = AzureQueueTaskDispatcher(
        FakeQueueClient(send_side_effect=ServiceRequestError("dns failure"))
    )

    with pytest.raises(RelayOperationError) as caught:
        dispatcher.enqueue_task(make_task("task-17"))

    assert caught.value.retryable is True
    assert caught.value.operation == "enqueue relay task"


def test_queue_enqueue_serialises_relay_task_payload() -> None:
    queue = FakeQueueClient()
    dispatcher = AzureQueueTaskDispatcher(queue)

    dispatcher.enqueue_task(make_task("task-17", state=TaskState.RUNNING))

    assert queue.messages == [
        '{"action": "rebuild-search-index", "resourcePath": "/tasks/task-17", '
        '"state": "running", "taskId": "task-17"}'
    ]


def test_default_factories_wrap_real_sdk_clients_without_network_calls() -> None:
    retry_settings = AzureRetrySettings()
    credential = DummyCredential()

    blob_service = default_blob_service_factory(
        account_url="https://relay.blob.core.windows.net",
        credential=credential,
        retry_settings=retry_settings,
    )
    queue_service = default_queue_service_factory(
        account_url="https://relay.queue.core.windows.net",
        credential=credential,
        retry_settings=retry_settings,
    )

    blob_container = blob_service.get_container_client("tasks")
    queue_client = queue_service.get_queue_client("tasks")

    assert hasattr(blob_container, "upload_blob")
    assert hasattr(blob_container, "download_blob")
    assert hasattr(queue_client, "send_message")


def test_version_is_exposed() -> None:
    assert __version__
