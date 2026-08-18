from __future__ import annotations

def fletcher16(payload: bytes | bytearray | memoryview) -> int: ...
def parse_length_prefix(
    frame: bytes | bytearray | memoryview,
    maximum_frame_size: int = 1024 * 1024,
) -> int: ...
