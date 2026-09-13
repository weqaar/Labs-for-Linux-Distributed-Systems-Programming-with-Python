"""Native implementation loading and fallback wiring."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import import_module
from types import ModuleType
from typing import cast

from .pure import (
    ReadableBuffer,
)
from .pure import (
    fletcher16 as fletcher16_python,
)
from .pure import (
    parse_length_prefix as parse_length_prefix_python,
)

ChecksumFunction = Callable[[ReadableBuffer], int]
ParseLengthFunction = Callable[..., int]
ModuleLoader = Callable[[str], ModuleType]


@dataclass(frozen=True)
class FramingImplementation:
    """Named checksum and header parser implementation."""

    name: str
    fletcher16: ChecksumFunction
    parse_length_prefix: ParseLengthFunction


def _default_loader(name: str) -> ModuleType:
    return import_module(name)


def resolve_implementation(
    *,
    prefer_native: bool = True,
    loader: ModuleLoader = _default_loader,
) -> FramingImplementation:
    if prefer_native:
        try:
            native_module = loader("lab_14_native_extension._framing_native")
        except ImportError:
            native_module = None
        if native_module is not None:
            return FramingImplementation(
                name="native",
                fletcher16=cast(ChecksumFunction, getattr(native_module, "fletcher16")),
                parse_length_prefix=cast(
                    ParseLengthFunction,
                    getattr(native_module, "parse_length_prefix"),
                ),
            )
    return FramingImplementation(
        name="python",
        fletcher16=fletcher16_python,
        parse_length_prefix=parse_length_prefix_python,
    )


def has_native_extension() -> bool:
    return resolve_implementation().name == "native"
