"""
Static type checker for STL programs.

Runs after the optimizer and before the compiler. Walks the AST and infers
a type for every node. Returns a list of TypeDiagnostic objects — warnings
or errors — without halting compilation. The orchestrator decides whether
errors are fatal.

Type lattice (from most to least specific):
    StringType  IntType  BoolType  LambdaType
                    ↓
               UnknownType   ← when inference fails or type is dynamic

Rules:
  - Pipelines: output type of stage N must be compatible with input of stage N+1
  - Calls: each argument type must match the plugin's declared parameter type
  - Conditionals: both branches must produce compatible types
  - BinaryOp comparisons: both operands should be the same family
  - map/map_words: lambda must produce StringType (since we rejoin)

Pattern: Visitor (TypeCheckVisitor walks every node),
         Strategy (each plugin declares its own type signature).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Dict, List, Optional, Tuple

from stratum.lang.ast_nodes import (
    AstNode,
    AstVisitor,
    BinaryOp,
    Call,
    Conditional,
    Identifier,
    Lambda,
    LetBinding,
    MacroDef,
    MacroRef,
    NumberLiteral,
    Pipeline,
    Program,
    StringLiteral,
)

logger = logging.getLogger(__name__)


# ---- type definitions -------------------------------------------------------


class StlType(ABC):
    """Base class for all STL types."""

    @abstractmethod
    def is_compatible_with(self, other: "StlType") -> bool:
        """True if a value of `self` type can be used where `other` is expected."""
        ...

    @abstractmethod
    def __str__(self) -> str: ...

    def __repr__(self) -> str:
        return str(self)


class StringType(StlType):
    def is_compatible_with(self, other: "StlType") -> bool:
        return isinstance(other, (StringType, UnknownType))

    def __str__(self) -> str:
        return "String"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, StringType)

    def __hash__(self) -> int:
        return hash("String")


class IntType(StlType):
    def is_compatible_with(self, other: "StlType") -> bool:
        return isinstance(other, (IntType, StringType, UnknownType))
        # Int can coerce to String in STRATUM (everything is ultimately a string)

    def __str__(self) -> str:
        return "Int"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, IntType)

    def __hash__(self) -> int:
        return hash("Int")


class BoolType(StlType):
    def is_compatible_with(self, other: "StlType") -> bool:
        return isinstance(other, (BoolType, IntType, StringType, UnknownType))

    def __str__(self) -> str:
        return "Bool"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, BoolType)

    def __hash__(self) -> int:
        return hash("Bool")


@dataclass
class LambdaType(StlType):
    param_type: StlType
    return_type: StlType

    def is_compatible_with(self, other: "StlType") -> bool:
        if isinstance(other, UnknownType):
            return True
        if isinstance(other, LambdaType):
            return (
                other.param_type.is_compatible_with(self.param_type)
                and self.return_type.is_compatible_with(other.return_type)
            )
        return False

    def __str__(self) -> str:
        return f"({self.param_type} → {self.return_type})"

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, LambdaType)
            and self.param_type == other.param_type
            and self.return_type == other.return_type
        )

    def __hash__(self) -> int:
        return hash(("Lambda", self.param_type, self.return_type))


class UnknownType(StlType):
    """Used when a type cannot be statically determined. Never an error."""

    def is_compatible_with(self, other: "StlType") -> bool:
        return True

    def __str__(self) -> str:
        return "?"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, UnknownType)

    def __hash__(self) -> int:
        return hash("Unknown")


# Singletons for convenience
STRING = StringType()
INT = IntType()
BOOL = BoolType()
UNKNOWN = UnknownType()


# ---- diagnostics ------------------------------------------------------------


class Severity(Enum):
    WARNING = auto()
    ERROR   = auto()


@dataclass
class TypeDiagnostic:
    severity: Severity
    message: str
    node_type: str = ""

    def is_error(self) -> bool:
        return self.severity == Severity.ERROR

    def is_warning(self) -> bool:
        return self.severity == Severity.WARNING

    def __str__(self) -> str:
        tag = "ERROR" if self.is_error() else "WARNING"
        location = f" [{self.node_type}]" if self.node_type else ""
        return f"[TYPE {tag}{location}] {self.message}"


def _error(msg: str, node: Optional[AstNode] = None) -> TypeDiagnostic:
    return TypeDiagnostic(
        severity=Severity.ERROR,
        message=msg,
        node_type=type(node).__name__ if node else "",
    )


def _warning(msg: str, node: Optional[AstNode] = None) -> TypeDiagnostic:
    return TypeDiagnostic(
        severity=Severity.WARNING,
        message=msg,
        node_type=type(node).__name__ if node else "",
    )


# ---- plugin type signatures -------------------------------------------------

# Maps plugin name → (param_types, return_type)
# param_types: list of expected argument types beyond the implicit input
_PLUGIN_SIGNATURES: Dict[str, Tuple[List[StlType], StlType]] = {
    # case
    "upper":       ([], STRING),
    "lower":       ([], STRING),
    "title":       ([], STRING),
    "swapcase":    ([], STRING),
    "capitalize":  ([], STRING),
    "upper_first": ([], STRING),
    # cipher / encoding
    "rot13":         ([], STRING),
    "caesar":        ([INT], STRING),
    "base64_encode": ([], STRING),
    "base64_decode": ([], STRING),
    # structure
    "reverse":    ([], STRING),
    "mirror":     ([], STRING),
    "sort":       ([], STRING),
    "unique":     ([], STRING),
    "chunk":      ([INT], STRING),
    "repeat":     ([INT], STRING),
    "split":      ([STRING], STRING),
    "join":       ([STRING], STRING),
    # padding
    "pad_left":  ([INT, STRING], STRING),
    "pad_right": ([INT, STRING], STRING),
    "center":    ([INT, STRING], STRING),
    "truncate":  ([INT, STRING], STRING),
    "trim":      ([], STRING),
    "strip":     ([STRING], STRING),
    # analysis  (return a numeric string, typed as String in this system)
    "length":     ([], STRING),
    "word_count": ([], STRING),
    "char_count": ([], STRING),
    "is_upper":   ([], STRING),
    "is_lower":   ([], STRING),
    "is_numeric": ([], STRING),
    "count":      ([STRING], STRING),
    "contains":   ([STRING], STRING),
    # misc
    "identity":    ([], STRING),
    "append":      ([STRING], STRING),
    "prepend":     ([STRING], STRING),
    "replace":     ([STRING, STRING], STRING),
    "slice":       ([INT, INT], STRING),
    # higher-order (map/map_words get special handling)
    "map":       ([LambdaType(STRING, STRING)], STRING),
    "map_chars": ([LambdaType(STRING, STRING)], STRING),
    "map_words": ([LambdaType(STRING, STRING)], STRING),
}


# ---- type environment -------------------------------------------------------


class TypeEnvironment:
    """
    Scoped mapping from variable/macro names to their inferred types.
    Supports nested scopes for lambda bodies.
    """

    def __init__(self, parent: Optional["TypeEnvironment"] = None) -> None:
        self._bindings: Dict[str, StlType] = {}
        self._parent = parent

    def bind(self, name: str, typ: StlType) -> None:
        self._bindings[name] = typ

    def lookup(self, name: str) -> StlType:
        if name in self._bindings:
            return self._bindings[name]
        if self._parent:
            return self._parent.lookup(name)
        return UNKNOWN

    def child(self) -> "TypeEnvironment":
        return TypeEnvironment(parent=self)


# ---- type checker visitor ---------------------------------------------------


class TypeCheckVisitor(AstVisitor):
    """
    Walks the AST and infers a type for each node.
    Accumulates TypeDiagnostic objects for any type violations found.

    visit_* methods return the inferred StlType of the node they visit.
    """

    def __init__(self, env: Optional[TypeEnvironment] = None) -> None:
        self._env = env or TypeEnvironment()
        self._diagnostics: List[TypeDiagnostic] = []

    @property
    def diagnostics(self) -> List[TypeDiagnostic]:
        return list(self._diagnostics)

    def has_errors(self) -> bool:
        return any(d.is_error() for d in self._diagnostics)

    def _default(self, node: AstNode) -> StlType:
        return UNKNOWN

    # ---- node visitors -------------------------------------------------------

    def visit_program(self, node: Program) -> StlType:
        # First pass: register macro types
        for macro in node.macros:
            macro_type = self.visit(macro.body)
            self._env.bind(macro.name, macro_type)
        return self.visit(node.body) if node.body else UNKNOWN

    def visit_macro_def(self, node: MacroDef) -> StlType:
        return self.visit(node.body)

    def visit_pipeline(self, node: Pipeline) -> StlType:
        current_type: StlType = STRING  # input to first stage is always String
        for i, stage in enumerate(node.stages):
            stage_type = self.visit(stage)
            if not current_type.is_compatible_with(stage_type):
                self._diagnostics.append(_error(
                    f"Pipeline stage {i + 1}: expected input compatible with "
                    f"{stage_type}, but previous stage produces {current_type}",
                    stage,
                ))
            current_type = stage_type
        return current_type

    def visit_call(self, node: Call) -> StlType:
        sig = _PLUGIN_SIGNATURES.get(node.name)
        if sig is None:
            # Unknown plugin — can't type-check, warn and continue
            self._diagnostics.append(_warning(
                f"Unknown plugin '{node.name}' — cannot type-check arguments",
                node,
            ))
            return UNKNOWN

        expected_param_types, return_type = sig

        # Check keyword argument types
        for kw_name, kw_val_node in node.kwargs.items():
            kw_type = self.visit(kw_val_node)
            # Find the parameter in the signature (by position, since we only have types)
            # Simple approach: we just check the value is not wildly wrong
            if isinstance(kw_type, LambdaType) and node.name not in ("map", "map_chars", "map_words"):
                self._diagnostics.append(_warning(
                    f"Passing a lambda to '{node.name}' keyword arg '{kw_name}' "
                    f"— plugin does not accept lambdas",
                    node,
                ))

        # Check positional arg types
        for i, (arg_node, expected) in enumerate(zip(node.args, expected_param_types)):
            actual = self.visit(arg_node)
            if not actual.is_compatible_with(expected):
                self._diagnostics.append(_error(
                    f"'{node.name}' argument {i + 1}: expected {expected}, got {actual}",
                    arg_node,
                ))

        return return_type

    def visit_conditional(self, node: Conditional) -> StlType:
        cond_type = self.visit(node.condition)
        # Condition must be boolean-ish
        if not isinstance(cond_type, (BoolType, IntType, UnknownType)):
            self._diagnostics.append(_warning(
                f"Conditional test has type {cond_type}; expected Bool or Int",
                node.condition,
            ))

        then_type = self.visit(node.then_branch)
        else_type = self.visit(node.else_branch)

        if not then_type.is_compatible_with(else_type) and not else_type.is_compatible_with(then_type):
            self._diagnostics.append(_warning(
                f"Conditional branches have incompatible types: "
                f"then={then_type}, else={else_type}",
                node,
            ))
            return UNKNOWN

        # Return the more specific type
        if isinstance(then_type, UnknownType):
            return else_type
        return then_type

    def visit_let_binding(self, node: LetBinding) -> StlType:
        value_type = self.visit(node.value)
        self._env.bind(node.name, value_type)
        if node.body:
            return self.visit(node.body)
        return value_type

    def visit_macro_ref(self, node: MacroRef) -> StlType:
        return self._env.lookup(node.name)

    def visit_identifier(self, node: Identifier) -> StlType:
        bound = self._env.lookup(node.name)
        if not isinstance(bound, UnknownType):
            return bound
        # Check if it's a known plugin
        sig = _PLUGIN_SIGNATURES.get(node.name)
        if sig is not None:
            return sig[1]
        return UNKNOWN

    def visit_string_literal(self, node: StringLiteral) -> StlType:
        return STRING

    def visit_number_literal(self, node: NumberLiteral) -> StlType:
        if isinstance(node.value, float):
            return UNKNOWN  # could be int-ish
        return INT

    def visit_binary_op(self, node: BinaryOp) -> StlType:
        left_type  = self.visit(node.left)
        right_type = self.visit(node.right)

        if node.op in ("+",):
            if isinstance(left_type, IntType) and isinstance(right_type, IntType):
                return INT
            return STRING  # string concatenation

        # Comparison operators return Bool
        if not isinstance(left_type, UnknownType) and not isinstance(right_type, UnknownType):
            if type(left_type) != type(right_type):
                # Allow String vs Int since length() returns String compared with Int literal
                if not (isinstance(left_type, (StringType, IntType)) and
                        isinstance(right_type, (StringType, IntType))):
                    self._diagnostics.append(_warning(
                        f"Comparing values of different types: "
                        f"{left_type} {node.op} {right_type}",
                        node,
                    ))
        return BOOL

    def visit_lambda(self, node: Lambda) -> StlType:
        # Type-check the lambda body in a child environment
        child_env = self._env.child()
        child_env.bind(node.param, STRING)  # lambda params are always String in STRATUM
        child_visitor = TypeCheckVisitor(env=child_env)
        return_type = child_visitor.visit(node.body)
        # Propagate diagnostics up
        self._diagnostics.extend(child_visitor.diagnostics)
        return LambdaType(param_type=STRING, return_type=return_type)


# ---- public API -------------------------------------------------------------


@dataclass
class TypeCheckResult:
    """Result of running the type checker over a Program."""
    inferred_type: StlType
    diagnostics: List[TypeDiagnostic] = field(default_factory=list)

    @property
    def errors(self) -> List[TypeDiagnostic]:
        return [d for d in self.diagnostics if d.is_error()]

    @property
    def warnings(self) -> List[TypeDiagnostic]:
        return [d for d in self.diagnostics if d.is_warning()]

    @property
    def is_clean(self) -> bool:
        return not self.diagnostics

    def summary(self) -> str:
        if self.is_clean:
            return f"OK  inferred type: {self.inferred_type}"
        lines = [f"inferred type: {self.inferred_type}"]
        for d in self.diagnostics:
            lines.append(str(d))
        return "\n".join(lines)


class TypeChecker:
    """
    Entry point for static type analysis.

    Usage:
        checker = TypeChecker()
        result = checker.check(program)
        if result.errors:
            # report or reject
    """

    def check(self, program: Program) -> TypeCheckResult:
        env = TypeEnvironment()
        visitor = TypeCheckVisitor(env=env)
        inferred = visitor.visit(program)
        return TypeCheckResult(
            inferred_type=inferred,
            diagnostics=visitor.diagnostics,
        )
