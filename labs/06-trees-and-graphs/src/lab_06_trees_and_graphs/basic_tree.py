"""General and binary-search trees used to teach tree operations."""

from __future__ import annotations

from collections import deque
from collections.abc import Hashable
from dataclasses import dataclass, field
from typing import Generic, Protocol, TypeVar

from typing_extensions import Self

T = TypeVar("T", bound=Hashable)
V = TypeVar("V")


class Comparable(Protocol):
    """Value supporting the ordering operation required by the tree."""

    def __lt__(self, other: Self, /) -> bool: ...


K = TypeVar("K", bound=Comparable)


@dataclass(slots=True)
class TreeNode(Generic[T]):
    """One value and its ordered child references."""

    value: T
    children: list[TreeNode[T]] = field(default_factory=list)

    def add_child(self, child: TreeNode[T]) -> None:
        self.children.append(child)


class Tree(Generic[T]):
    """Rooted ordered tree with unique values."""

    def __init__(self, root_value: T) -> None:
        self.root = TreeNode(root_value)
        self._nodes: dict[T, TreeNode[T]] = {root_value: self.root}

    def add(self, parent: T, value: T) -> None:
        if value in self._nodes:
            raise ValueError(f"tree value already exists: {value}")
        try:
            parent_node = self._nodes[parent]
        except KeyError as exc:
            raise LookupError(f"tree value not found: {parent}") from exc
        node = TreeNode(value)
        parent_node.add_child(node)
        self._nodes[value] = node

    def find(self, value: T) -> TreeNode[T]:
        try:
            return self._nodes[value]
        except KeyError as exc:
            raise LookupError(f"tree value not found: {value}") from exc

    def replace(self, old: T, new: T) -> None:
        if new in self._nodes:
            raise ValueError(f"tree value already exists: {new}")
        node = self.find(old)
        del self._nodes[old]
        node.value = new
        self._nodes[new] = node

    def remove_subtree(self, value: T) -> tuple[T, ...]:
        if value == self.root.value:
            raise ValueError("use a new Tree to replace the root")
        parent = self._parent_of(value)
        node = self.find(value)
        removed = self._preorder_from(node)
        parent.children.remove(node)
        for removed_value in removed:
            del self._nodes[removed_value]
        return removed

    def preorder(self) -> tuple[T, ...]:
        return self._preorder_from(self.root)

    def postorder(self) -> tuple[T, ...]:
        values: list[T] = []

        def visit(node: TreeNode[T]) -> None:
            for child in node.children:
                visit(child)
            values.append(node.value)

        visit(self.root)
        return tuple(values)

    def breadth_first(self) -> tuple[T, ...]:
        queue = deque([self.root])
        values: list[T] = []
        while queue:
            node = queue.popleft()
            values.append(node.value)
            queue.extend(node.children)
        return tuple(values)

    def height(self) -> int:
        queue = deque([(self.root, 0)])
        maximum = 0
        while queue:
            node, depth = queue.popleft()
            maximum = max(maximum, depth)
            queue.extend((child, depth + 1) for child in node.children)
        return maximum

    def _parent_of(self, value: T) -> TreeNode[T]:
        for node in self._nodes.values():
            if any(child.value == value for child in node.children):
                return node
        raise LookupError(f"tree value not found: {value}")

    @staticmethod
    def _preorder_from(node: TreeNode[T]) -> tuple[T, ...]:
        values = [node.value]
        for child in node.children:
            values.extend(Tree._preorder_from(child))
        return tuple(values)


@dataclass(slots=True)
class _BinaryNode(Generic[K, V]):
    key: K
    value: V
    left: _BinaryNode[K, V] | None = None
    right: _BinaryNode[K, V] | None = None


class BinarySearchTree(Generic[K, V]):
    """Unbalanced ordered map exposing fundamental search-tree operations."""

    def __init__(self) -> None:
        self._root: _BinaryNode[K, V] | None = None
        self._size = 0

    def __len__(self) -> int:
        return self._size

    def put(self, key: K, value: V) -> None:
        self._root, inserted = self._put(self._root, key, value)
        if inserted:
            self._size += 1

    def get(self, key: K) -> V:
        node = self._root
        while node is not None:
            if key < node.key:
                node = node.left
            elif node.key < key:
                node = node.right
            else:
                return node.value
        raise KeyError(key)

    def delete(self, key: K) -> V:
        removed: list[V] = []
        self._root = self._delete(self._root, key, removed)
        if not removed:
            raise KeyError(key)
        self._size -= 1
        return removed[0]

    def replace_key(self, old: K, new: K) -> None:
        if old != new:
            try:
                self.get(new)
            except KeyError:
                pass
            else:
                raise ValueError(f"tree key already exists: {new}")
        value = self.delete(old)
        self.put(new, value)

    def inorder(self) -> tuple[tuple[K, V], ...]:
        values: list[tuple[K, V]] = []

        def visit(node: _BinaryNode[K, V] | None) -> None:
            if node is None:
                return
            visit(node.left)
            values.append((node.key, node.value))
            visit(node.right)

        visit(self._root)
        return tuple(values)

    def preorder(self) -> tuple[K, ...]:
        values: list[K] = []

        def visit(node: _BinaryNode[K, V] | None) -> None:
            if node is None:
                return
            values.append(node.key)
            visit(node.left)
            visit(node.right)

        visit(self._root)
        return tuple(values)

    def postorder(self) -> tuple[K, ...]:
        values: list[K] = []

        def visit(node: _BinaryNode[K, V] | None) -> None:
            if node is None:
                return
            visit(node.left)
            visit(node.right)
            values.append(node.key)

        visit(self._root)
        return tuple(values)

    def level_order(self) -> tuple[K, ...]:
        if self._root is None:
            return ()
        queue = deque([self._root])
        values: list[K] = []
        while queue:
            node = queue.popleft()
            values.append(node.key)
            if node.left is not None:
                queue.append(node.left)
            if node.right is not None:
                queue.append(node.right)
        return tuple(values)

    def height(self) -> int:
        def node_height(node: _BinaryNode[K, V] | None) -> int:
            if node is None:
                return -1
            return 1 + max(node_height(node.left), node_height(node.right))

        return node_height(self._root)

    def rebalance(self) -> None:
        ordered = self.inorder()

        def build(start: int, end: int) -> _BinaryNode[K, V] | None:
            if start >= end:
                return None
            middle = (start + end) // 2
            key, value = ordered[middle]
            node = _BinaryNode(key, value)
            node.left = build(start, middle)
            node.right = build(middle + 1, end)
            return node

        self._root = build(0, len(ordered))

    def _put(
        self,
        node: _BinaryNode[K, V] | None,
        key: K,
        value: V,
    ) -> tuple[_BinaryNode[K, V], bool]:
        if node is None:
            return _BinaryNode(key, value), True
        if key < node.key:
            node.left, inserted = self._put(node.left, key, value)
        elif node.key < key:
            node.right, inserted = self._put(node.right, key, value)
        else:
            node.value = value
            inserted = False
        return node, inserted

    def _delete(
        self,
        node: _BinaryNode[K, V] | None,
        key: K,
        removed: list[V],
    ) -> _BinaryNode[K, V] | None:
        if node is None:
            return None
        if key < node.key:
            node.left = self._delete(node.left, key, removed)
            return node
        if node.key < key:
            node.right = self._delete(node.right, key, removed)
            return node
        removed.append(node.value)
        if node.left is None:
            return node.right
        if node.right is None:
            return node.left
        successor = node.right
        while successor.left is not None:
            successor = successor.left
        node.key, node.value = successor.key, successor.value
        node.right = self._delete_successor(node.right, successor.key)
        return node

    def _delete_successor(
        self,
        node: _BinaryNode[K, V] | None,
        key: K,
    ) -> _BinaryNode[K, V] | None:
        if node is None:
            return None
        if key < node.key:
            node.left = self._delete_successor(node.left, key)
            return node
        return node.right
