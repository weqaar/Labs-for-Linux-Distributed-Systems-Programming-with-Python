"""A deterministic real-socket ZeroMQ PUB/SUB round trip."""

from __future__ import annotations

from uuid import uuid4

import zmq


def pubsub_round_trip(
    topic: bytes, payload: bytes, *, timeout_ms: int = 1_000
) -> tuple[bytes, bytes]:
    """Use XPUB subscription acknowledgement to avoid slow-joiner sleeps."""

    if not topic:
        raise ValueError("topic must not be empty")
    context = zmq.Context()
    publisher = context.socket(zmq.XPUB)
    subscriber = context.socket(zmq.SUB)
    endpoint = f"inproc://relay-{uuid4()}"
    try:
        publisher.setsockopt(zmq.LINGER, 0)
        subscriber.setsockopt(zmq.LINGER, 0)
        publisher.setsockopt(zmq.RCVTIMEO, timeout_ms)
        subscriber.setsockopt(zmq.RCVTIMEO, timeout_ms)
        publisher.bind(endpoint)
        subscriber.connect(endpoint)
        subscriber.setsockopt(zmq.SUBSCRIBE, topic)

        subscription = publisher.recv()
        if subscription != b"\x01" + topic:
            raise RuntimeError("publisher received an unexpected subscription")
        publisher.send_multipart((topic, payload))
        received = subscriber.recv_multipart()
        return received[0], received[1]
    finally:
        subscriber.close()
        publisher.close()
        context.term()
