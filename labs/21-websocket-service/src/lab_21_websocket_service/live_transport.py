"""Loopback GraphQL-over-WebSocket transport for the relay lab."""

from __future__ import annotations

import json
from typing import Any, cast

from websockets.asyncio.client import connect
from websockets.asyncio.server import ServerConnection, serve
from websockets.typing import Subprotocol

from lab_21_websocket_service.graphql_api import RelayGraphQL


async def graphql_websocket_round_trip(
    api: RelayGraphQL,
    document: str,
    *,
    scopes: frozenset[str],
) -> tuple[dict[str, object], ...]:
    """Run one GraphQL operation over a real graphql-transport-ws connection."""

    async def handler(connection: ServerConnection) -> None:
        initial = _decode(await connection.recv())
        if initial.get("type") != "connection_init":
            await connection.close(code=4400, reason="connection_init required")
            return
        await connection.send(json.dumps({"type": "connection_ack"}))

        request = _decode(await connection.recv())
        if request.get("type") != "subscribe" or not isinstance(request.get("id"), str):
            await connection.close(code=4400, reason="subscribe required")
            return
        payload = request.get("payload")
        if not isinstance(payload, dict) or not isinstance(payload.get("query"), str):
            await connection.close(code=4400, reason="query required")
            return
        result = api.execute(payload["query"], scopes=scopes)
        result_payload: dict[str, object] = {}
        if result.data is not None:
            result_payload["data"] = cast(dict[str, object], result.data)
        if result.errors:
            result_payload["errors"] = [{"message": error.message} for error in result.errors]
        await connection.send(
            json.dumps(
                {
                    "type": "next",
                    "id": request["id"],
                    "payload": result_payload,
                }
            )
        )
        await connection.send(json.dumps({"type": "complete", "id": request["id"]}))

    frames: list[dict[str, object]] = []
    graphql_transport = Subprotocol("graphql-transport-ws")
    async with serve(
        handler,
        "127.0.0.1",
        0,
        subprotocols=(graphql_transport,),
    ) as server:
        port = next(iter(server.sockets)).getsockname()[1]
        async with connect(
            f"ws://127.0.0.1:{port}",
            subprotocols=(graphql_transport,),
        ) as connection:
            await connection.send(json.dumps({"type": "connection_init"}))
            frames.append(_decode(await connection.recv()))
            await connection.send(
                json.dumps(
                    {
                        "type": "subscribe",
                        "id": "operation-1",
                        "payload": {"query": document},
                    }
                )
            )
            frames.append(_decode(await connection.recv()))
            frames.append(_decode(await connection.recv()))
    return tuple(frames)


def _decode(message: str | bytes) -> dict[str, Any]:
    if isinstance(message, bytes):
        message = message.decode("utf-8")
    decoded = json.loads(message)
    if not isinstance(decoded, dict):
        raise ValueError("WebSocket frame must contain a JSON object")
    return cast(dict[str, Any], decoded)
