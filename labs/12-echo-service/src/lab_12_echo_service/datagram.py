"""A loopback UDP echo server and client with explicit application retries."""

from __future__ import annotations

import socket
import threading
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class DatagramExchange:
    """Result of one UDP request, including how many sends were required."""

    response: bytes
    attempts: int


class DatagramEchoServer:
    """Echo complete UDP datagrams and optionally discard the first one."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 0,
        *,
        socket_timeout: float = 0.1,
        drop_first: bool = False,
    ) -> None:
        if socket_timeout <= 0:
            raise ValueError("socket_timeout must be positive")
        self._host = host
        self._port = port
        self._socket_timeout = socket_timeout
        self._drop_first = drop_first
        self._dropped = False
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stop_requested = threading.Event()
        self._ready = threading.Event()
        self._datagrams: list[bytes] = []
        self._lock = threading.Lock()
        self._address: tuple[str, int] | None = None

    @property
    def address(self) -> tuple[str, int]:
        if self._address is None:
            raise RuntimeError("server has not started")
        return self._address

    @property
    def received_datagrams(self) -> list[bytes]:
        with self._lock:
            return list(self._datagrams)

    def start(self) -> None:
        if self._socket is not None:
            raise RuntimeError("server already started")
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        server_socket.settimeout(self._socket_timeout)
        server_socket.bind((self._host, self._port))
        self._socket = server_socket
        self._address = server_socket.getsockname()
        self._thread = threading.Thread(
            target=self._serve,
            name="relay-datagram-echo",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(1.0):
            raise TimeoutError("datagram echo server did not become ready")

    def close(self) -> None:
        self._stop_requested.set()
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        if self._thread is not None:
            self._thread.join(1.0)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _serve(self) -> None:
        self._ready.set()
        assert self._socket is not None
        server_socket = self._socket
        while not self._stop_requested.is_set():
            try:
                payload, peer = server_socket.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                break
            with self._lock:
                self._datagrams.append(payload)
            if self._drop_first and not self._dropped:
                self._dropped = True
                continue
            try:
                server_socket.sendto(payload, peer)
            except OSError:
                break


class DatagramEchoClient:
    """Exchange one UDP datagram with bounded timeout-based retries."""

    def __init__(self, host: str, port: int, *, socket_timeout: float = 0.1) -> None:
        if socket_timeout <= 0:
            raise ValueError("socket_timeout must be positive")
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.settimeout(socket_timeout)
        self._socket.connect((host, port))

    def __enter__(self) -> DatagramEchoClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def exchange(self, payload: bytes, *, max_attempts: int = 1) -> DatagramExchange:
        if not payload:
            raise ValueError("payload must not be empty")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")

        for attempt in range(1, max_attempts + 1):
            self._socket.send(payload)
            try:
                response = self._socket.recv(65535)
            except TimeoutError:
                if attempt == max_attempts:
                    raise TimeoutError(f"no UDP response after {max_attempts} attempt(s)") from None
                continue
            return DatagramExchange(response=response, attempts=attempt)
        raise AssertionError("retry loop completed without returning or raising")

    def close(self) -> None:
        self._socket.close()
