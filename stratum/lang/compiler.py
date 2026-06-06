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
from stratum.vm.opcodes import Bytecode, BytecodeBuilder, LambdaObject, Opcode

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
            # A bare lambda in a pipeline is applied to the input via CALL_LAMBDA
            assert self._regs is not None and self._builder is not None
            lambda_obj = self._compile_lambda_body(stage)
            lambda_reg = self._regs.alloc()
            self._builder.emit_load_const(lambda_reg, lambda_obj)
            dest = self._regs.alloc()
            self._builder.emit_call_lambda(dest, lambda_reg, input_reg)
            return dest

        # Fallback: visit the node (might be a literal used as transform)
        return self.visit(stage)

    def _compile_call_with_input(self, node: Call, input_reg: int) -> int:
        assert self._regs is not None and self._builder is not None

        # Special case: map / map_chars with a lambda argument → MAP_CHARS
        if node.name in ("map", "map_chars") and node.args and isinstance(node.args[0], Lambda):
            return self._compile_map(node.args[0], input_reg, mode="chars")

        # Special case: map_words with a lambda argument → MAP_WORDS
        if node.name == "map_words" and node.args and isinstance(node.args[0], Lambda):
            return self._compile_map(node.args[0], input_reg, mode="words")

        dest = self._regs.alloc()
        arg_regs = [input_reg] + [self.visit(a) for a in node.args]
        kwarg_regs = {k: self.visit(v) for k, v in node.kwargs.items()}
        self._builder.emit_call_plugin(dest, node.name, arg_regs, kwarg_regs)
        return dest

    def _compile_map(self, lambda_node: Lambda, input_reg: int, mode: str) -> int:
        """Compile map(char => ...) or map_words(word => ...) into MAP_CHARS/MAP_WORDS."""
        assert self._regs is not None and self._builder is not None
        lambda_obj = self._compile_lambda_body(lambda_node)
        lambda_reg = self._regs.alloc()
        self._builder.emit_load_const(lambda_reg, lambda_obj)
        dest = self._regs.alloc()
        if mode == "chars":
            self._builder.emit_map_chars(dest, input_reg, lambda_reg)
        else:
            self._builder.emit_map_words(dest, input_reg, lambda_reg)
        return dest

    def _compile_lambda_body(self, lambda_node: Lambda) -> LambdaObject:
        """
        Compile a Lambda AST node into a standalone LambdaObject.

        The lambda body is compiled by a child Compiler that has:
          - R0 = the lambda's input value
          - The lambda parameter pre-bound as a variable at R0
          - All macros from the parent scope inherited

        The resulting Bytecode is wrapped in a LambdaObject and stored
        as a LOAD_CONST value in the parent bytecode.
        """
        child = Compiler(max_registers=self._max_registers)
        child._builder = BytecodeBuilder(source=f"λ{lambda_node.param}")
        child._regs = RegisterAllocator(max_registers=self._max_registers)
        child._symbols = SymbolTable()
        child._label_counter = self._label_counter  # avoid label name collisions

        # Inherit parent macros
        for name, body in self._symbols.macros.items():
            child._symbols.define_macro(name, body)

        # R0 = input; bind the parameter name as a variable pointing at R0
        child._regs.alloc()  # consume R0
        child._symbols.define_variable(lambda_node.param)
        child._builder.emit_store_var(lambda_node.param, 0)

        # Compile body — result_reg holds the lambda's return value
        result_reg = child.visit(lambda_node.body)
        child._builder.emit_halt(result_reg)

        # Propagate label counter back so parent labels stay unique
        self._label_counter = child._label_counter

        return LambdaObject(param=lambda_node.param, bytecode=child._builder.build())

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
        """
        A lambda that appears as a standalone expression (not inside map/map_words)
        is compiled into a LambdaObject constant, then applied to the current input
        via CALL_LAMBDA. This handles cases like: `char => upper(char)` as a program.
        """
        assert self._regs is not None and self._builder is not None
        lambda_obj = self._compile_lambda_body(node)
        lambda_reg = self._regs.alloc()
        self._builder.emit_load_const(lambda_reg, lambda_obj)
        dest = self._regs.alloc()
        self._builder.emit_call_lambda(dest, lambda_reg, self.INPUT_REGISTER)
        return dest

    def visit_pipeline(self, node: Pipeline) -> int:
        current_reg = self.INPUT_REGISTER
        for stage in node.stages:
            current_reg = self._compile_stage(stage, current_reg)
        return current_reg
