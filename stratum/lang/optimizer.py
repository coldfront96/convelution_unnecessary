"""
AST optimizer. Applies multiple passes over the AST to simplify it
before compilation. Each pass is a Strategy that implements OptimizationPass.

Passes run repeatedly until the tree stabilizes (or max_iterations).

Pattern: Strategy (each pass is interchangeable),
         Visitor (each pass walks the tree),
         Template Method (BasePass.run handles the iteration logic).
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Type

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


@dataclass
class PassResult:
    pass_name: str
    nodes_removed: int = 0
    nodes_rewritten: int = 0

    @property
    def changed(self) -> bool:
        return self.nodes_removed > 0 or self.nodes_rewritten > 0


class OptimizationPass(ABC):
    """Strategy interface for an optimizer pass."""

    @property
    @abstractmethod
    def name(self) -> str: ...

    @abstractmethod
    def apply(self, node: AstNode) -> tuple[AstNode, PassResult]: ...


# ---- pass: constant folding -------------------------------------------------


class ConstantFoldPass(OptimizationPass):
    """
    Evaluate binary operations on two numeric literals at compile time.
    `3 + 5` → `8`, `10 > 3` → `1` (true).
    """

    @property
    def name(self) -> str:
        return "constant_fold"

    def apply(self, node: AstNode) -> tuple[AstNode, PassResult]:
        visitor = _ConstantFoldVisitor()
        result = visitor.visit(node)
        return result, PassResult(
            pass_name=self.name,
            nodes_rewritten=visitor.rewritten,
        )


class _ConstantFoldVisitor(AstVisitor):
    def __init__(self) -> None:
        self.rewritten = 0

    def _default(self, node: AstNode) -> AstNode:
        return node

    def visit_binary_op(self, node: BinaryOp) -> AstNode:
        left = self.visit(node.left)
        right = self.visit(node.right)

        if isinstance(left, NumberLiteral) and isinstance(right, NumberLiteral):
            result = self._eval(node.op, left.value, right.value)
            if result is not None:
                self.rewritten += 1
                return NumberLiteral(value=result)

        return BinaryOp(op=node.op, left=left, right=right)

    def _eval(self, op: str, l: int | float, r: int | float) -> Optional[int | float]:
        try:
            if op == "+":  return l + r
            if op == "-":  return l - r
            if op == "*":  return l * r
            if op == "==": return int(l == r)
            if op == "!=": return int(l != r)
            if op == "<":  return int(l < r)
            if op == "<=": return int(l <= r)
            if op == ">":  return int(l > r)
            if op == ">=": return int(l >= r)
        except Exception:
            pass
        return None

    def visit_pipeline(self, node: Pipeline) -> AstNode:
        stages = [self.visit(s) for s in node.stages]
        return Pipeline(stages=stages)

    def visit_call(self, node: Call) -> AstNode:
        args = [self.visit(a) for a in node.args]
        kwargs = {k: self.visit(v) for k, v in node.kwargs.items()}
        return Call(name=node.name, args=args, kwargs=kwargs)

    def visit_conditional(self, node: Conditional) -> AstNode:
        condition = self.visit(node.condition)
        then_branch = self.visit(node.then_branch)
        else_branch = self.visit(node.else_branch)
        # If condition is a literal, fold the entire conditional
        if isinstance(condition, NumberLiteral):
            self.rewritten += 1
            return then_branch if condition.value else else_branch
        return Conditional(condition=condition, then_branch=then_branch, else_branch=else_branch)

    def visit_let_binding(self, node: LetBinding) -> AstNode:
        value = self.visit(node.value)
        body = self.visit(node.body) if node.body else None
        return LetBinding(name=node.name, value=value, body=body)

    def visit_program(self, node: Program) -> AstNode:
        macros = [MacroDef(name=m.name, body=self.visit(m.body)) for m in node.macros]
        body = self.visit(node.body) if node.body else None
        return Program(macros=macros, body=body)

    def visit_lambda(self, node: Lambda) -> AstNode:
        return Lambda(param=node.param, body=self.visit(node.body))


# ---- pass: dead code elimination --------------------------------------------


class DeadCodePass(OptimizationPass):
    """
    Remove unreachable branches.
    Also removes macros that are never referenced.
    """

    @property
    def name(self) -> str:
        return "dead_code"

    def apply(self, node: AstNode) -> tuple[AstNode, PassResult]:
        visitor = _DeadCodeVisitor()
        result = visitor.visit(node)
        return result, PassResult(
            pass_name=self.name,
            nodes_removed=visitor.removed,
        )


class _DeadCodeVisitor(AstVisitor):
    def __init__(self) -> None:
        self.removed = 0

    def _default(self, node: AstNode) -> AstNode:
        return node

    def visit_program(self, node: Program) -> AstNode:
        # Collect referenced macro names from body
        body = self.visit(node.body) if node.body else None
        if body is None:
            return Program(macros=[], body=None)
        referenced = _collect_macro_refs(body)
        live_macros: List[MacroDef] = []
        for m in node.macros:
            if m.name in referenced:
                live_macros.append(MacroDef(name=m.name, body=self.visit(m.body)))
            else:
                self.removed += 1
                logger.debug("dead_code: removing unused macro %r", m.name)
        return Program(macros=live_macros, body=body)

    def visit_pipeline(self, node: Pipeline) -> AstNode:
        stages = [self.visit(s) for s in node.stages]
        return Pipeline(stages=stages)

    def visit_call(self, node: Call) -> AstNode:
        args = [self.visit(a) for a in node.args]
        kwargs = {k: self.visit(v) for k, v in node.kwargs.items()}
        return Call(name=node.name, args=args, kwargs=kwargs)

    def visit_conditional(self, node: Conditional) -> AstNode:
        return Conditional(
            condition=self.visit(node.condition),
            then_branch=self.visit(node.then_branch),
            else_branch=self.visit(node.else_branch),
        )

    def visit_let_binding(self, node: LetBinding) -> AstNode:
        value = self.visit(node.value)
        body = self.visit(node.body) if node.body else None
        return LetBinding(name=node.name, value=value, body=body)

    def visit_lambda(self, node: Lambda) -> AstNode:
        return Lambda(param=node.param, body=self.visit(node.body))


def _collect_macro_refs(node: AstNode) -> set[str]:
    refs: set[str] = set()
    if isinstance(node, MacroRef):
        refs.add(node.name)
    elif isinstance(node, Identifier):
        # Identifiers may resolve to macros at compile time; collect conservatively.
        refs.add(node.name)
    for child in node.children():
        refs |= _collect_macro_refs(child)
    return refs


# ---- pass: peephole ---------------------------------------------------------


class PeepholePass(OptimizationPass):
    """
    Local rewrites on small patterns:
    - Single-stage pipeline → unwrap to the stage itself
    - identity in a pipeline → remove it
    - identity alone → keep (it's a valid no-op transform)
    """

    @property
    def name(self) -> str:
        return "peephole"

    def apply(self, node: AstNode) -> tuple[AstNode, PassResult]:
        visitor = _PeepholeVisitor()
        result = visitor.visit(node)
        return result, PassResult(
            pass_name=self.name,
            nodes_rewritten=visitor.rewritten,
            nodes_removed=visitor.removed,
        )


class _PeepholeVisitor(AstVisitor):
    def __init__(self) -> None:
        self.rewritten = 0
        self.removed = 0

    def _default(self, node: AstNode) -> AstNode:
        return node

    def visit_pipeline(self, node: Pipeline) -> AstNode:
        stages = [self.visit(s) for s in node.stages]
        # remove identity stages from multi-stage pipelines
        filtered = [
            s for s in stages
            if not (isinstance(s, Identifier) and s.name == "identity")
        ]
        if len(stages) != len(filtered):
            self.removed += len(stages) - len(filtered)
            stages = filtered if filtered else stages  # don't produce empty pipeline

        if len(stages) == 1:
            self.rewritten += 1
            return stages[0]
        return Pipeline(stages=stages)

    def visit_call(self, node: Call) -> AstNode:
        args = [self.visit(a) for a in node.args]
        kwargs = {k: self.visit(v) for k, v in node.kwargs.items()}
        return Call(name=node.name, args=args, kwargs=kwargs)

    def visit_conditional(self, node: Conditional) -> AstNode:
        return Conditional(
            condition=self.visit(node.condition),
            then_branch=self.visit(node.then_branch),
            else_branch=self.visit(node.else_branch),
        )

    def visit_let_binding(self, node: LetBinding) -> AstNode:
        value = self.visit(node.value)
        body = self.visit(node.body) if node.body else None
        return LetBinding(name=node.name, value=value, body=body)

    def visit_program(self, node: Program) -> AstNode:
        macros = [MacroDef(name=m.name, body=self.visit(m.body)) for m in node.macros]
        body = self.visit(node.body) if node.body else None
        return Program(macros=macros, body=body)

    def visit_lambda(self, node: Lambda) -> AstNode:
        return Lambda(param=node.param, body=self.visit(node.body))


# ---- pass: pipeline merge ---------------------------------------------------


class PipelineMergePass(OptimizationPass):
    """
    Flatten nested pipelines: `(a | b) | c` → `a | b | c`.
    """

    @property
    def name(self) -> str:
        return "pipeline_merge"

    def apply(self, node: AstNode) -> tuple[AstNode, PassResult]:
        visitor = _PipelineMergeVisitor()
        result = visitor.visit(node)
        return result, PassResult(
            pass_name=self.name,
            nodes_rewritten=visitor.rewritten,
        )


class _PipelineMergeVisitor(AstVisitor):
    def __init__(self) -> None:
        self.rewritten = 0

    def _default(self, node: AstNode) -> AstNode:
        return node

    def visit_pipeline(self, node: Pipeline) -> AstNode:
        flat: List[AstNode] = []
        for stage in node.stages:
            visited = self.visit(stage)
            if isinstance(visited, Pipeline):
                self.rewritten += 1
                flat.extend(visited.stages)
            else:
                flat.append(visited)
        if len(flat) == 1:
            return flat[0]
        return Pipeline(stages=flat)

    def visit_call(self, node: Call) -> AstNode:
        args = [self.visit(a) for a in node.args]
        kwargs = {k: self.visit(v) for k, v in node.kwargs.items()}
        return Call(name=node.name, args=args, kwargs=kwargs)

    def visit_conditional(self, node: Conditional) -> AstNode:
        return Conditional(
            condition=self.visit(node.condition),
            then_branch=self.visit(node.then_branch),
            else_branch=self.visit(node.else_branch),
        )

    def visit_program(self, node: Program) -> AstNode:
        macros = [MacroDef(name=m.name, body=self.visit(m.body)) for m in node.macros]
        body = self.visit(node.body) if node.body else None
        return Program(macros=macros, body=body)

    def visit_let_binding(self, node: LetBinding) -> AstNode:
        value = self.visit(node.value)
        body = self.visit(node.body) if node.body else None
        return LetBinding(name=node.name, value=value, body=body)

    def visit_lambda(self, node: Lambda) -> AstNode:
        return Lambda(param=node.param, body=self.visit(node.body))


# ---- pass registry + optimizer ----------------------------------------------


_PASS_CLASSES: Dict[str, Type[OptimizationPass]] = {
    "constant_fold":  ConstantFoldPass,
    "dead_code":      DeadCodePass,
    "peephole":       PeepholePass,
    "pipeline_merge": PipelineMergePass,
}


class Optimizer:
    """
    Runs a configurable sequence of passes until the AST stabilizes
    or max_iterations is reached.

    Pattern: Strategy (selects and sequences pass objects from config).
    """

    def __init__(
        self,
        passes: Optional[List[str]] = None,
        max_iterations: int = 3,
        enabled: bool = True,
    ) -> None:
        self._enabled = enabled
        self._max_iterations = max_iterations
        pass_names = passes or list(_PASS_CLASSES.keys())
        self._passes: List[OptimizationPass] = []
        for name in pass_names:
            cls = _PASS_CLASSES.get(name)
            if cls is None:
                raise ValueError(f"Unknown optimizer pass: {name!r}")
            self._passes.append(cls())

    def optimize(self, program: Program) -> tuple[Program, List[PassResult]]:
        if not self._enabled:
            return program, []

        all_results: List[PassResult] = []
        current: AstNode = program

        for iteration in range(self._max_iterations):
            changed = False
            for opt_pass in self._passes:
                current, result = opt_pass.apply(current)
                all_results.append(result)
                if result.changed:
                    changed = True
                    logger.debug(
                        "optimizer pass %s iter %d: -%d nodes, ~%d rewrites",
                        result.pass_name,
                        iteration,
                        result.nodes_removed,
                        result.nodes_rewritten,
                    )
            if not changed:
                logger.debug("optimizer stabilized after %d iterations", iteration + 1)
                break

        assert isinstance(current, Program)
        return current, all_results
