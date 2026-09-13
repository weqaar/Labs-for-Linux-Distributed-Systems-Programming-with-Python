"""Left-leaning red-black search tree for relay priority indexes."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Generic, Protocol, TypeVar

from typing_extensions import Self


class Comparable(Protocol):
    def __lt__(self, other: Self, /) -> bool: ...


K = TypeVar("K", bound=Comparable)
V = TypeVar("V")


class Color(str, Enum):
    RED = "red"
    BLACK = "black"


@dataclass
class _Node(Generic[K, V]):
    key: K
    value: V
    color: Color = Color.RED
    left: _Node[K, V] | None = None
    right: _Node[K, V] | None = None


class RedBlackTree(Generic[K, V]):
    """Balanced ordered map with logarithmic search and insertion."""

    def __init__(self) -> None:
        self._root: _Node[K, V] | None = None
        self._size = 0

    def __len__(self) -> int:
        return self._size

    def put(self, key: K, value: V) -> None:
        self._root, inserted = self._insert(self._root, key, value)
        self._root.color = Color.BLACK
        if inserted:
            self._size += 1

    def get(self, key: K) -> V:
        node = self._root
        while node is not None:
            if self._less(key, node.key):
                node = node.left
            elif self._less(node.key, key):
                node = node.right
            else:
                return node.value
        raise KeyError(key)

    def items(self) -> tuple[tuple[K, V], ...]:
        ordered: list[tuple[K, V]] = []

        def visit(node: _Node[K, V] | None) -> None:
            if node is None:
                return
            visit(node.left)
            ordered.append((node.key, node.value))
            visit(node.right)

        visit(self._root)
        return tuple(ordered)

    def validate(self) -> int:
        """Validate search and red-black invariants, returning black height."""

        if self._root is not None and self._root.color is not Color.BLACK:
            raise AssertionError("root must be black")

        def inspect(
            node: _Node[K, V] | None,
            lower: K | None,
            upper: K | None,
        ) -> int:
            if node is None:
                return 1
            if lower is not None and not self._less(lower, node.key):
                raise AssertionError("tree violates lower key bound")
            if upper is not None and not self._less(node.key, upper):
                raise AssertionError("tree violates upper key bound")
            if self._is_red(node.right):
                raise AssertionError("red links must lean left")
            if self._is_red(node) and self._is_red(node.left):
                raise AssertionError("consecutive red links are forbidden")
            left_height = inspect(node.left, lower, node.key)
            right_height = inspect(node.right, node.key, upper)
            if left_height != right_height:
                raise AssertionError("root-to-leaf black heights differ")
            return left_height + (node.color is Color.BLACK)

        return inspect(self._root, None, None)

    def _insert(
        self,
        node: _Node[K, V] | None,
        key: K,
        value: V,
    ) -> tuple[_Node[K, V], bool]:
        if node is None:
            return _Node(key, value), True

        inserted = False
        if self._less(key, node.key):
            node.left, inserted = self._insert(node.left, key, value)
        elif self._less(node.key, key):
            node.right, inserted = self._insert(node.right, key, value)
        else:
            node.value = value

        if self._is_red(node.right) and not self._is_red(node.left):
            node = self._rotate_left(node)
        left = node.left
        if self._is_red(left) and left is not None and self._is_red(left.left):
            node = self._rotate_right(node)
        if self._is_red(node.left) and self._is_red(node.right):
            self._flip_colors(node)
        return node, inserted

    @staticmethod
    def _is_red(node: _Node[K, V] | None) -> bool:
        return node is not None and node.color is Color.RED

    @staticmethod
    def _less(left: K, right: K) -> bool:
        return left < right

    @staticmethod
    def _rotate_left(node: _Node[K, V]) -> _Node[K, V]:
        replacement = node.right
        if replacement is None:
            raise AssertionError("left rotation requires a right child")
        node.right = replacement.left
        replacement.left = node
        replacement.color = node.color
        node.color = Color.RED
        return replacement

    @staticmethod
    def _rotate_right(node: _Node[K, V]) -> _Node[K, V]:
        replacement = node.left
        if replacement is None:
            raise AssertionError("right rotation requires a left child")
        node.left = replacement.right
        replacement.right = node
        replacement.color = node.color
        node.color = Color.RED
        return replacement

    @staticmethod
    def _flip_colors(node: _Node[K, V]) -> None:
        if node.left is None or node.right is None:
            raise AssertionError("color flip requires two children")
        node.color = Color.RED if node.color is Color.BLACK else Color.BLACK
        node.left.color = Color.BLACK if node.left.color is Color.RED else Color.RED
        node.right.color = Color.BLACK if node.right.color is Color.RED else Color.RED
