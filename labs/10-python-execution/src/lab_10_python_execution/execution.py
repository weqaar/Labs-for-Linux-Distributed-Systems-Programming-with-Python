"""Inspect the representations CPython builds from source."""

from __future__ import annotations

import ast
import dis
import importlib.machinery
import importlib.util
import io
import platform
import sys
import tokenize
from dataclasses import dataclass
from types import CodeType


@dataclass(frozen=True, slots=True)
class SourceToken:
    """One significant token emitted by Python's tokenizer."""

    kind: str
    text: str
    line: int
    column: int


@dataclass(frozen=True, slots=True)
class AstNode:
    """One node in a preorder outline of Python's abstract syntax tree."""

    path: str
    kind: str
    depth: int
    line: int | None
    column: int | None


@dataclass(frozen=True, slots=True)
class SourceReport:
    """Tokenizer, AST, and module-code evidence for one source string."""

    tokens: tuple[SourceToken, ...]
    ast_root: str
    ast_nodes: tuple[AstNode, ...]
    names: tuple[str, ...]
    constants: tuple[object, ...]


@dataclass(frozen=True, slots=True)
class BytecodeInstruction:
    """Stable subset of a version-specific disassembly instruction."""

    offset: int
    opname: str
    argument: object


@dataclass(frozen=True, slots=True)
class FunctionBytecode:
    """Code-object and instruction evidence for one function."""

    name: str
    positional_arguments: int
    local_names: tuple[str, ...]
    constants: tuple[object, ...]
    instructions: tuple[BytecodeInstruction, ...]


@dataclass(frozen=True, slots=True)
class ArchitectureReport:
    """Interpreter and native-boundary facts for the current process."""

    implementation: str
    version: tuple[int, int, int]
    cache_tag: str | None
    bytecode_magic: bytes
    machine: str
    operating_system: str
    extension_suffixes: tuple[str, ...]


def tokenize_source(source: str) -> tuple[SourceToken, ...]:
    """Return positioned lexical tokens without parsing or executing source."""

    if not source.strip():
        raise ValueError("source must not be empty")
    generated = tokenize.generate_tokens(io.StringIO(source).readline)
    ignored = {
        tokenize.ENCODING,
        tokenize.ENDMARKER,
        tokenize.NEWLINE,
        tokenize.NL,
    }
    return tuple(
        SourceToken(
            kind=tokenize.tok_name[token.type],
            text=token.string,
            line=token.start[0],
            column=token.start[1],
        )
        for token in generated
        if token.type not in ignored
    )


def inspect_source(source: str) -> SourceReport:
    """Tokenize, parse, and compile source without executing it."""

    tokens = tokenize_source(source)
    tree = ast.parse(source, filename="<relay>", mode="exec")
    code = compile(tree, filename="<relay>", mode="exec")
    return SourceReport(
        tokens=tokens,
        ast_root=type(tree).__name__,
        ast_nodes=tuple(_outline_ast(tree)),
        names=code.co_names,
        constants=code.co_consts,
    )


def inspect_function(function: object) -> FunctionBytecode:
    """Return selected code-object fields and decoded instructions."""

    code = getattr(function, "__code__", None)
    if not isinstance(code, CodeType):
        raise TypeError("inspect_function requires a Python function")
    instructions = tuple(
        BytecodeInstruction(item.offset, item.opname, item.argval)
        for item in dis.get_instructions(code)
    )
    return FunctionBytecode(
        name=code.co_name,
        positional_arguments=code.co_argcount,
        local_names=code.co_varnames,
        constants=code.co_consts,
        instructions=instructions,
    )


def architecture_report() -> ArchitectureReport:
    """Describe which artifacts are portable and which are target-native."""

    return ArchitectureReport(
        implementation=sys.implementation.name,
        version=sys.version_info[:3],
        cache_tag=sys.implementation.cache_tag,
        bytecode_magic=importlib.util.MAGIC_NUMBER,
        machine=platform.machine(),
        operating_system=platform.system(),
        extension_suffixes=tuple(importlib.machinery.EXTENSION_SUFFIXES),
    )


def relay_transition(state: str, succeeded: bool) -> str:
    """Small relay function used for AST and bytecode inspection."""

    if state != "running":
        raise ValueError("only a running task may complete")
    return "succeeded" if succeeded else "failed"


def _outline_ast(
    node: ast.AST,
    *,
    path: str = "root",
    depth: int = 0,
) -> tuple[AstNode, ...]:
    current = AstNode(
        path=path,
        kind=type(node).__name__,
        depth=depth,
        line=getattr(node, "lineno", None),
        column=getattr(node, "col_offset", None),
    )
    descendants: list[AstNode] = [current]
    for field_name, value in ast.iter_fields(node):
        if isinstance(value, ast.AST):
            descendants.extend(_outline_ast(value, path=f"{path}.{field_name}", depth=depth + 1))
        elif isinstance(value, list):
            for index, child in enumerate(value):
                if isinstance(child, ast.AST):
                    descendants.extend(
                        _outline_ast(
                            child,
                            path=f"{path}.{field_name}[{index}]",
                            depth=depth + 1,
                        )
                    )
    return tuple(descendants)
