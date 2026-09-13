"""Typed relay query language built with PyParsing."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, TypeAlias

from pyparsing import (
    CaselessKeyword,
    ParseBaseException,
    ParserElement,
    ParseResults,
    QuotedString,
    Word,
    alphanums,
    infix_notation,
    one_of,
    opAssoc,
    pyparsing_common,
)

QueryValue: TypeAlias = str | int


class TaskView(Protocol):
    @property
    def task_id(self) -> str: ...

    @property
    def action(self) -> str: ...

    @property
    def state(self) -> str: ...

    @property
    def priority(self) -> int: ...

    @property
    def attempts(self) -> int: ...


class Expression(Protocol):
    def matches(self, task: TaskView) -> bool: ...


class QuerySyntaxError(ValueError):
    """Raised when task query text does not satisfy the complete grammar."""


@dataclass(frozen=True, slots=True)
class Predicate:
    field: str
    operator: str
    value: QueryValue

    def matches(self, task: TaskView) -> bool:
        actual = getattr(task, self.field)
        if self.operator == "=":
            return actual == self.value
        if self.operator == "!=":
            return actual != self.value
        if self.operator == "contains":
            if not isinstance(actual, str) or not isinstance(self.value, str):
                raise TypeError("contains requires string operands")
            return self.value in actual
        if not isinstance(actual, int) or not isinstance(self.value, int):
            raise TypeError(f"{self.operator} requires integer operands")
        if self.operator == "<":
            return actual < self.value
        if self.operator == "<=":
            return actual <= self.value
        if self.operator == ">":
            return actual > self.value
        if self.operator == ">=":
            return actual >= self.value
        raise AssertionError(f"parser produced unknown operator: {self.operator}")


@dataclass(frozen=True, slots=True)
class Not:
    expression: Expression

    def matches(self, task: TaskView) -> bool:
        return not self.expression.matches(task)


@dataclass(frozen=True, slots=True)
class And:
    expressions: tuple[Expression, ...]

    def matches(self, task: TaskView) -> bool:
        return all(expression.matches(task) for expression in self.expressions)


@dataclass(frozen=True, slots=True)
class Or:
    expressions: tuple[Expression, ...]

    def matches(self, task: TaskView) -> bool:
        return any(expression.matches(task) for expression in self.expressions)


def _predicate(tokens: ParseResults) -> Predicate:
    values = list(tokens)
    return Predicate(str(values[0]), str(values[1]).lower(), values[2])


def _unary(tokens: ParseResults) -> Not:
    values = list(tokens)[0]
    return Not(values[1])


def _and(tokens: ParseResults) -> And:
    values = list(tokens)[0]
    return And(tuple(values[0::2]))


def _or(tokens: ParseResults) -> Or:
    values = list(tokens)[0]
    return Or(tuple(values[0::2]))


def build_query_parser() -> ParserElement:
    """Build an independent parser so callers cannot mutate shared grammar state."""

    ParserElement.enable_packrat()
    text_field = one_of("task_id action state")
    number_field = one_of("priority attempts")
    quoted = QuotedString('"', esc_char="\\")
    bare_word = Word(alphanums + "_-./")
    integer = pyparsing_common.signed_integer
    text_predicate = (
        text_field + one_of("= != contains", caseless=True) + (quoted | bare_word)
    ).set_parse_action(_predicate)
    number_predicate = (
        number_field + one_of("= != < <= > >=", caseless=True) + integer
    ).set_parse_action(_predicate)
    predicate = text_predicate | number_predicate
    return infix_notation(
        predicate,
        [
            (CaselessKeyword("not"), 1, opAssoc.RIGHT, _unary),
            (CaselessKeyword("and"), 2, opAssoc.LEFT, _and),
            (CaselessKeyword("or"), 2, opAssoc.LEFT, _or),
        ],
    )


def parse_query(source: str) -> Expression:
    """Parse all query text and return a typed, executable syntax tree."""

    try:
        parsed = build_query_parser().parse_string(source, parse_all=True)
    except ParseBaseException as exc:
        raise QuerySyntaxError(f"invalid query at column {exc.col}: {exc.msg}") from exc
    expression = parsed[0]
    if not isinstance(expression, (Predicate, Not, And, Or)):
        raise AssertionError("query parser did not produce an expression")
    return expression


def select_tasks(tasks: Sequence[TaskView], source: str) -> tuple[TaskView, ...]:
    expression = parse_query(source)
    return tuple(task for task in tasks if expression.matches(task))
