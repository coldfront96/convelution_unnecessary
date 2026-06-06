"""
Abstract Syntax Tree node definitions for STL.

All nodes are immutable dataclasses.
Tree traversal uses the Visitor pattern — nodes don't know about
their consumers; visitors dispatch on node type.

Pattern: Visitor (AstVisitor), Composite (Pipeline contains nodes).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ---- visitor interface ------------------------------------------------------


class AstVisitor(ABC):
    """
    Visitor pattern over the AST.
    Each visit_* method corresponds to one node type.
    Default implementations call a generic visit() for easy catch-all handling.
    """

    def visit(self, node: "AstNode") -> Any:
        return node.accept(self)

    def visit_pipeline(self, node: "Pipeline") -> Any:
        return self._default(node)

    def visit_call(self, node: "Call") -> Any:
        return self._default(node)

    def visit_conditional(self, node: "Conditional") -> Any:
        return self._default(node)

    def visit_let_binding(self, node: "LetBinding") -> Any:
        return self._default(node)

    def visit_macro_def(self, node: "MacroDef") -> Any:
        return self._default(node)

    def visit_macro_ref(self, node: "MacroRef") -> Any:
        return self._default(node)

    def visit_identifier(self, node: "Identifier") -> Any:
        return self._default(node)

    def visit_string_literal(self, node: "StringLiteral") -> Any:
        return self._default(node)

    def visit_number_literal(self, node: "NumberLiteral") -> Any:
        return self._default(node)

    def visit_binary_op(self, node: "BinaryOp") -> Any:
        return self._default(node)

    def visit_lambda(self, node: "Lambda") -> Any:
        return self._default(node)

    def visit_program(self, node: "Program") -> Any:
        return self._default(node)

    def _default(self, node: "AstNode") -> Any:
        return None


# ---- base node --------------------------------------------------------------


class AstNode(ABC):
    """Base class for all AST nodes."""

    @abstractmethod
    def accept(self, visitor: AstVisitor) -> Any: ...

    @abstractmethod
    def children(self) -> List["AstNode"]: ...

    def pretty(self, indent: int = 0) -> str:
        prefix = "  " * indent
        name = type(self).__name__
        child_lines = [c.pretty(indent + 1) for c in self.children()]
        if not child_lines:
            return f"{prefix}{name}({self._inline_repr()})"
        inner = "\n".join(child_lines)
        return f"{prefix}{name}({self._inline_repr()})\n{inner}"

    def _inline_repr(self) -> str:
        return ""

    def node_count(self) -> int:
        return 1 + sum(c.node_count() for c in self.children())


# ---- concrete nodes ---------------------------------------------------------


@dataclass
class Program(AstNode):
    """Root node. Contains macro definitions and the main expression."""
    macros: List["MacroDef"] = field(default_factory=list)
    body: Optional[AstNode] = None

    def accept(self, visitor: AstVisitor) -> Any:
        return visitor.visit_program(self)

    def children(self) -> List[AstNode]:
        result: List[AstNode] = list(self.macros)
        if self.body:
            result.append(self.body)
        return result


@dataclass
class Pipeline(AstNode):
    """
    Composite of sequential transforms: `upper | reverse | rot13`.
    Each stage's output feeds into the next stage's input.
    """
    stages: List[AstNode] = field(default_factory=list)

    def accept(self, visitor: AstVisitor) -> Any:
        return visitor.visit_pipeline(self)

    def children(self) -> List[AstNode]:
        return list(self.stages)

    def _inline_repr(self) -> str:
        return f"stages={len(self.stages)}"


@dataclass
class Call(AstNode):
    """
    A transform call with optional positional and keyword arguments.
    `pad(width=20, char=".")`
    """
    name: str = ""
    args: List[AstNode] = field(default_factory=list)
    kwargs: Dict[str, AstNode] = field(default_factory=dict)

    def accept(self, visitor: AstVisitor) -> Any:
        return visitor.visit_call(self)

    def children(self) -> List[AstNode]:
        return list(self.args) + list(self.kwargs.values())

    def _inline_repr(self) -> str:
        return f"name={self.name!r}"


@dataclass
class Conditional(AstNode):
    """
    `if <condition> then <then_branch> else <else_branch>`
    The else branch is required.
    """
    condition: AstNode = field(default_factory=lambda: NumberLiteral(value=0))
    then_branch: AstNode = field(default_factory=lambda: Identifier(name="identity"))
    else_branch: AstNode = field(default_factory=lambda: Identifier(name="identity"))

    def accept(self, visitor: AstVisitor) -> Any:
        return visitor.visit_conditional(self)

    def children(self) -> List[AstNode]:
        return [self.condition, self.then_branch, self.else_branch]


@dataclass
class LetBinding(AstNode):
    """
    `let x = length`
    Binds the result of an expression to a variable name for use
    in subsequent transforms.
    """
    name: str = ""
    value: AstNode = field(default_factory=lambda: NumberLiteral(value=0))
    body: Optional[AstNode] = None  # expression that uses the binding

    def accept(self, visitor: AstVisitor) -> Any:
        return visitor.visit_let_binding(self)

    def children(self) -> List[AstNode]:
        result = [self.value]
        if self.body:
            result.append(self.body)
        return result

    def _inline_repr(self) -> str:
        return f"name={self.name!r}"


@dataclass
class MacroDef(AstNode):
    """
    `macro shout = upper | trim | append("!!!")`
    Defines a named reusable pipeline.
    """
    name: str = ""
    body: AstNode = field(default_factory=lambda: Identifier(name="identity"))

    def accept(self, visitor: AstVisitor) -> Any:
        return visitor.visit_macro_def(self)

    def children(self) -> List[AstNode]:
        return [self.body]

    def _inline_repr(self) -> str:
        return f"name={self.name!r}"


@dataclass
class MacroRef(AstNode):
    """Reference to a previously defined macro."""
    name: str = ""

    def accept(self, visitor: AstVisitor) -> Any:
        return visitor.visit_macro_ref(self)

    def children(self) -> List[AstNode]:
        return []

    def _inline_repr(self) -> str:
        return f"name={self.name!r}"


@dataclass
class Identifier(AstNode):
    """
    A bare identifier — either a zero-arg transform call or a variable reference.
    Resolved at compile time once the symbol table is known.
    """
    name: str = ""

    def accept(self, visitor: AstVisitor) -> Any:
        return visitor.visit_identifier(self)

    def children(self) -> List[AstNode]:
        return []

    def _inline_repr(self) -> str:
        return f"name={self.name!r}"


@dataclass
class StringLiteral(AstNode):
    value: str = ""

    def accept(self, visitor: AstVisitor) -> Any:
        return visitor.visit_string_literal(self)

    def children(self) -> List[AstNode]:
        return []

    def _inline_repr(self) -> str:
        return repr(self.value)


@dataclass
class NumberLiteral(AstNode):
    value: int | float = 0

    def accept(self, visitor: AstVisitor) -> Any:
        return visitor.visit_number_literal(self)

    def children(self) -> List[AstNode]:
        return []

    def _inline_repr(self) -> str:
        return str(self.value)


@dataclass
class BinaryOp(AstNode):
    """
    Binary comparison or arithmetic: `length > 10`, `length + 5`.
    Used in Conditional conditions.
    """
    op: str = "=="
    left: AstNode = field(default_factory=lambda: NumberLiteral(value=0))
    right: AstNode = field(default_factory=lambda: NumberLiteral(value=0))

    def accept(self, visitor: AstVisitor) -> Any:
        return visitor.visit_binary_op(self)

    def children(self) -> List[AstNode]:
        return [self.left, self.right]

    def _inline_repr(self) -> str:
        return f"op={self.op!r}"


@dataclass
class Lambda(AstNode):
    """
    Inline lambda for per-character or per-word transforms.
    `map(char => rot13(char))`
    """
    param: str = ""
    body: AstNode = field(default_factory=lambda: Identifier(name="identity"))

    def accept(self, visitor: AstVisitor) -> Any:
        return visitor.visit_lambda(self)

    def children(self) -> List[AstNode]:
        return [self.body]

    def _inline_repr(self) -> str:
        return f"param={self.param!r}"


# ---- utility ----------------------------------------------------------------


class NodeCountVisitor(AstVisitor):
    """Counts total nodes in a tree."""

    def __init__(self) -> None:
        self.count = 0

    def _default(self, node: AstNode) -> None:
        self.count += 1
        for child in node.children():
            child.accept(self)


class PrettyPrintVisitor(AstVisitor):
    def __init__(self) -> None:
        self._lines: list[str] = []
        self._depth = 0

    def _default(self, node: AstNode) -> None:
        indent = "  " * self._depth
        self._lines.append(f"{indent}{type(node).__name__}({node._inline_repr()})")
        self._depth += 1
        for child in node.children():
            child.accept(self)
        self._depth -= 1

    def result(self) -> str:
        return "\n".join(self._lines)
