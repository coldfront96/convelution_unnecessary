"""
Compiler: walks the optimized AST and emits bytecode via BytecodeBuilder.

The compiler maintains a symbol table (macros + variables) and a
register allocator. It never calls plugins directly — it only emits
CALL_PLUGIN instructions that the VM will dispatch at runtime.

Pattern: Visitor (compiles each node type separately).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

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
from stratum.vm.opcodes import Bytecode, BytecodeBuilder, Opcode

logger = logging.getLogger(__name__)


class CompileError(Exception):
    pass


@dataclass
class SymbolTable:
    """Tracks macro definitions and variable bindings during compilation."""
    macros: Dict[str, AstNode] = field(default_factory=dict)
    variables: Set[str] = field(default_factory=set)

    def define_macro(self, name: str, body: AstNode) -> None:
        self.macros[name] = body

    def define_variable(self, name: str) -> None:
        self.variables.add(name)

    def is_macro(self, name: str) -> bool:
        return name in self.macros

    def is_variable(self, name: str) -> bool:
        return name in self.variables


class RegisterAllocator:
    """
    Simple bump-pointer register allocator.
    Registers are never freed during a single compilation unit —
    complexity over efficiency is the mandate here.
    """

    def __init__(self, max_registers: int = 16) -> None:
        self._next = 0
        self._max = max_registers
        self._named: Dict[str, int] = {}

    def alloc(self) -> int:
        if self._next >= self._max:
            raise CompileError(
                f"Register exhaustion: more than {self._max} registers needed. "
                "Consider simplifying your program (just kidding)."
            )
        reg = self._next
        self._next += 1
        return reg

    def alloc_named(self, name: str) -> int:
        if name not in self._named:
            self._named[name] = self.alloc()
        return self._named[name]

    def get_named(self, name: str) -> Optional[int]:
        return self._named.get(name)

    @property
    def hwm(self) -> int:
        return self._next


class Compiler(AstVisitor):
    """
    AST → Bytecode.

    Each visit_* method compiles a node and returns the register
    that holds the node's result. The final result register is
    used in the HALT instruction.

    The input string is always pre-loaded into R0 at the start.
    """

    INPUT_REGISTER = 0

    def __init__(self, max_registers: int = 16) -> None:
        self._builder: Optional[BytecodeBuilder] = None
        self._symbols = SymbolTable()
        self._regs: Optional[RegisterAllocator] = None
        self._max_registers = max_registers
        self._label_counter = 0

    def compile(self, program: Program, source: str = "") -> Bytecode:
        self._builder = BytecodeBuilder(source=source)
        self._regs = RegisterAllocator(max_registers=self._max_registers)
        self._symbols = SymbolTable()

        # Register 0 is always the input string — pre-allocate it
        self._regs.alloc()  # R0 = input

        # First pass: register all macros in symbol table
        for macro in program.macros:
            self._symbols.define_macro(macro.name, macro.body)

        # Compile the body
        if program.body is None:
            # Empty program — just return the input
            self._builder.emit_halt(self.INPUT_REGISTER)
        else:
            result_reg = self.visit(program.body)
            self._builder.emit_halt(result_reg)

        return self._builder.build()

    def _fresh_label(self, prefix: str = "L") -> str:
        self._label_counter += 1
        return f"{prefix}_{self._label_counter}"

    # ---- visitor methods ----------------------------------------------------

    def visit_program(self, node: Program) -> int:
        raise CompileError("visit_program should not be called directly")

    def visit_pipeline(self, node: Pipeline) -> int:
        assert self._regs is not None and self._builder is not None
        # Feed the first stage the input register; each stage feeds next
        current_reg = self.INPUT_REGISTER
        for stage in node.stages:
            # Load current value as the implicit input for the stage
            if current_reg != self.INPUT_REGISTER:
                pass  # already in a register
            result_reg = self._compile_stage(stage, current_reg)
            current_reg = result_reg
        return current_reg

    def _compile_stage(self, stage: AstNode, input_reg: int) -> int:
        assert self._regs is not None and self._builder is not None

        if isinstance(stage, Identifier):
            # Bare identifier — either a macro ref or a zero-arg plugin call
            if self._symbols.is_macro(stage.name):
                return self._compile_macro_inline(stage.name, input_reg)
            if self._symbols.is_variable(stage.name):
                dest = self._regs.alloc()
                self._builder.emit_load_var(dest, stage.name)
                return dest
            # Treat as zero-arg plugin call with current value as input
            dest = self._regs.alloc()
            self._builder.emit_call_plugin(dest, stage.name, [input_reg], {})
            return dest

        if isinstance(stage, Call):
            return self._compile_call_with_input(stage, input_reg)

        if isinstance(stage, MacroRef):
            return self._compile_macro_inline(stage.name, input_reg)

        if isinstance(stage, Conditional):
            return self._compile_conditional_with_input(stage, input_reg)

        if isinstance(stage, Lambda):
            # Lambda in a pipeline applies the body per-character (simplified: treat as body)
            return self._compile_stage(stage.body, input_reg)

        # Fallback: visit the node (might be a literal used as transform)
        return self.visit(stage)

    def _compile_call_with_input(self, node: Call, input_reg: int) -> int:
        assert self._regs is not None and self._builder is not None
        dest = self._regs.alloc()
        arg_regs = [input_reg] + [self.visit(a) for a in node.args]
        kwarg_regs = {k: self.visit(v) for k, v in node.kwargs.items()}
        self._builder.emit_call_plugin(dest, node.name, arg_regs, kwarg_regs)
        return dest

    def _compile_macro_inline(self, name: str, input_reg: int) -> int:
        """Inline-expand a macro at the call site."""
        body = self._symbols.macros[name]
        # Temporarily redirect the input register context
        saved_input = self.INPUT_REGISTER
        # We can't really change INPUT_REGISTER (it's a class constant),
        # so we MOV the current input into R0 if it isn't already there.
        assert self._builder is not None and self._regs is not None
        if input_reg != self.INPUT_REGISTER:
            self._builder.emit_mov(self.INPUT_REGISTER, input_reg)
        return self._compile_stage(body, self.INPUT_REGISTER)

    def _compile_conditional_with_input(
        self, node: Conditional, input_reg: int
    ) -> int:
        assert self._regs is not None and self._builder is not None

        label_else = self._fresh_label("else")
        label_end  = self._fresh_label("end")

        # Evaluate condition
        cond_reg = self.visit(node.condition)

        # JUMP_IF_FALSE over then-branch
        self._builder.emit_jump_if_false(cond_reg, label_else)

        # Allocate result register (shared between branches)
        result_reg = self._regs.alloc()

        # Then branch
        then_reg = self._compile_stage(node.then_branch, input_reg)
        self._builder.emit_mov(result_reg, then_reg)
        self._builder.emit_jump(label_end)

        # Else branch
        self._builder.label(label_else)
        else_reg = self._compile_stage(node.else_branch, input_reg)
        self._builder.emit_mov(result_reg, else_reg)

        self._builder.label(label_end)
        return result_reg

    def visit_call(self, node: Call) -> int:
        return self._compile_call_with_input(node, self.INPUT_REGISTER)

    def visit_identifier(self, node: Identifier) -> int:
        assert self._regs is not None and self._builder is not None
        if self._symbols.is_macro(node.name):
            return self._compile_macro_inline(node.name, self.INPUT_REGISTER)
        if self._symbols.is_variable(node.name):
            dest = self._regs.alloc()
            self._builder.emit_load_var(dest, node.name)
            return dest
        # Bare name treated as zero-arg plugin call on input
        dest = self._regs.alloc()
        self._builder.emit_call_plugin(dest, node.name, [self.INPUT_REGISTER], {})
        return dest

    def visit_macro_ref(self, node: MacroRef) -> int:
        return self._compile_macro_inline(node.name, self.INPUT_REGISTER)

    def visit_macro_def(self, node: MacroDef) -> int:
        raise CompileError("MacroDef nodes should not appear in the body")

    def visit_string_literal(self, node: StringLiteral) -> int:
        assert self._regs is not None and self._builder is not None
        dest = self._regs.alloc()
        self._builder.emit_load_const(dest, node.value)
        return dest

    def visit_number_literal(self, node: NumberLiteral) -> int:
        assert self._regs is not None and self._builder is not None
        dest = self._regs.alloc()
        self._builder.emit_load_const(dest, node.value)
        return dest

    def visit_binary_op(self, node: BinaryOp) -> int:
        assert self._regs is not None and self._builder is not None
        if node.op == "+":
            left_reg = self.visit(node.left)
            right_reg = self.visit(node.right)
            dest = self._regs.alloc()
            self._builder.emit_add(dest, left_reg, right_reg)
            return dest

        # Comparison
        left_reg = self.visit(node.left)
        right_reg = self.visit(node.right)
        dest = self._regs.alloc()
        self._builder.emit_compare(dest, node.op, left_reg, right_reg)
        return dest

    def visit_conditional(self, node: Conditional) -> int:
        return self._compile_conditional_with_input(node, self.INPUT_REGISTER)

    def visit_let_binding(self, node: LetBinding) -> int:
        assert self._regs is not None and self._builder is not None
        # Evaluate the value
        value_reg = self.visit(node.value)
        # Store into variable
        self._builder.emit_store_var(node.name, value_reg)
        self._symbols.define_variable(node.name)
        # Compile body if present, otherwise return the value
        if node.body:
            return self._compile_stage(node.body, self.INPUT_REGISTER)
        return value_reg

    def visit_lambda(self, node: Lambda) -> int:
        # Simplified: compile lambda body as if param is the input register
        assert self._regs is not None and self._builder is not None
        self._symbols.define_variable(node.param)
        self._builder.emit_store_var(node.param, self.INPUT_REGISTER)
        return self._compile_stage(node.body, self.INPUT_REGISTER)

    def visit_pipeline(self, node: Pipeline) -> int:
        current_reg = self.INPUT_REGISTER
        for stage in node.stages:
            current_reg = self._compile_stage(stage, current_reg)
        return current_reg
