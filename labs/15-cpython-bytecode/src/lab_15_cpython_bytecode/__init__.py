"""Parser and source-build contracts for the custom CPython checkpoint."""

from __future__ import annotations

from .query import (
    And,
    Not,
    Or,
    Predicate,
    QuerySyntaxError,
    parse_query,
    select_tasks,
)
from .source import (
    CPYTHON_COMMIT,
    CPYTHON_TAG,
    BuildStep,
    CPythonSource,
    build_plan,
    verify_patch_contract,
)

__version__ = "0.1.0"

__all__ = [
    "And",
    "BuildStep",
    "CPYTHON_COMMIT",
    "CPYTHON_TAG",
    "CPythonSource",
    "Not",
    "Or",
    "Predicate",
    "QuerySyntaxError",
    "__version__",
    "build_plan",
    "parse_query",
    "select_tasks",
    "verify_patch_contract",
]
