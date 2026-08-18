"""A real loopback TCP echo server and client."""

from __future__ import annotations

import socket
import threading
import time


class EchoServer:
    """Echo bytes over loopback while recording read boundaries."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        *,
        read_size: int = 4096,
        socket_timeout: float = 0.2,
    ) -> None:
        if read_size <= 0:
            raise ValueError("read_size must be positive")
        if socket_timeout <= 0:
            raise ValueError("socket_timeout must be positive")
        self._host = host
        self._port = port
        self._read_size = read_size
        self._socket_timeout = socket_timeout
        self._listener: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._stop_requested = threading.Event()
        self._ready = threading.Event()
        self._read_condition = threading.Condition()
        self._observed_reads: list[bytes] = []
        self._clients: set[socket.socket] = set()
        self._client_threads: set[threading.Thread] = set()
        self._client_lock = threading.Lock()
        self._address: tuple[str, int] | None = None

    @property
    def address(self) -> tuple[str, int]:
        if self._address is None:
            raise RuntimeError("server has not started")
        return self._address

    @property
    def observed_reads(self) -> list[bytes]:
        with self._read_condition:
            return list(self._observed_reads)

    def start(self) -> None:
        if self._listener is not None:
            raise RuntimeError("server already started")
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.settimeout(self._socket_timeout)
        listener.bind((self._host, self._port))
        listener.listen()
        self._listener = listener
        self._address = listener.getsockname()
        self._accept_thread = threading.Thread(
            target=self._accept_loop,
            name="relay-echo-listener",
            daemon=True,
        )
        self._accept_thread.start()
        if not self._ready.wait(1.0):
            raise TimeoutError("echo server did not become ready")

    def wait_for_reads(self, count: int, timeout: float = 1.0) -> bool:
        deadline = time.monotonic() + timeout
        with self._read_condition:
            while len(self._observed_reads) < count:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._read_condition.wait(remaining)
            return True

    def is_running(self) -> bool:
        return self._accept_thread is not None and self._accept_thread.is_alive()

    def close(self) -> None:
        self._stop_requested.set()
        if self._listener is not None:
            try:
                self._listener.close()
            finally:
                self._listener = None
        with self._client_lock:
            clients = list(self._clients)
            threads = list(self._client_threads)
        for client in clients:
            try:
                client.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            client.close()
        if self._accept_thread is not None:
            self._accept_thread.join(1.0)
        for thread in threads:
            thread.join(1.0)

    def _accept_loop(self) -> None:
        self._ready.set()
        assert self._listener is not None
        while not self._stop_requested.is_set():
            try:
                client, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            client.settimeout(self._socket_timeout)
            thread = threading.Thread(
                target=self._handle_client,
                args=(client,),
                name="relay-echo-client",
                daemon=True,
            )
            with self._client_lock:
                self._clients.add(client)
                self._client_threads.add(thread)
            thread.start()

    def _handle_client(self, client: socket.socket) -> None:
        try:
            while not self._stop_requested.is_set():
                try:
                    chunk = client.recv(self._read_size)
                except TimeoutError:
                    continue
                except OSError:
                    break
                if not chunk:
                    break
                with self._read_condition:
                    self._observed_reads.append(chunk)
                    self._read_condition.notify_all()
                view = memoryview(chunk)
                while view:
                    try:
                        sent = client.send(view)
                    except OSError:
                        return
                    if sent <= 0:
                        raise ConnectionError("socket send made no forward progress")
                    view = view[sent:]
        finally:
            with self._client_lock:
                self._clients.discard(client)
                current = threading.current_thread()
                self._client_threads.discard(current)
            try:
                client.close()
            except OSError:
                pass


class EchoClient:
    """Small client helper with explicit socket timeouts."""

    def __init__(self, host: str, port: int, *, socket_timeout: float = 0.2) -> None:
        if socket_timeout <= 0:
            raise ValueError("socket_timeout must be positive")
        self._socket = socket.create_connection((host, port), timeout=socket_timeout)
        self._socket.settimeout(socket_timeout)

    def __enter__(self) -> EchoClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def send(self, payload: bytes) -> None:
        self._socket.sendall(payload)

    def shutdown_write(self) -> None:
        self._socket.shutdown(socket.SHUT_WR)

    def receive_exactly(self, expected_bytes: int) -> bytes:
        if expected_bytes < 0:
            raise ValueError("expected_bytes must be non-negative")
        chunks: list[bytes] = []
        remaining = expected_bytes
        while remaining:
            try:
                chunk = self._socket.recv(remaining)
            except TimeoutError as exc:
                raise TimeoutError("timed out waiting for echoed bytes") from exc
            if not chunk:
                raise ConnectionError("socket closed before the full echo arrived")
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def close(self) -> None:
        try:
            self._socket.close()
        except OSError:
            pass
