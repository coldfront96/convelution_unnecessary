"""
Bytecode instruction set for the STRATUM virtual machine.

The VM is register-based: instructions operate on named registers
(R0..RN) rather than a stack. This makes data-flow explicit in the
bytecode and allows the optimizer to do register allocation.

Pattern: Command — each Instruction is an immutable command record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Dict, List, Optional, Tuple


class Opcode(Enum):
    """All legal opcodes. Operand shapes are documented per variant."""

    # --- data movement ---
    LOAD_CONST = auto()    # (dest: int, value: Any)        — load literal into register
    LOAD_VAR   = auto()    # (dest: int, name: str)         — load variable into register
    STORE_VAR  = auto()    # (name: str, src: int)          — store register into variable
    MOV        = auto()    # (dest: int, src: int)          — copy register to register

    # --- plugin invocation ---
    CALL_PLUGIN = auto()   # (dest: int, name: str, args: List[int], kwargs: Dict[str, int])
                           #   args/kwargs are register indices

    # --- arithmetic/comparison (for condition expressions) ---
    GET_LENGTH  = auto()   # (dest: int, src: int)          — len(register[src])
    COMPARE     = auto()   # (dest: int, op: str, left: int, right: int)
                           #   op ∈ {"==", "!=", "<", "<=", ">", ">="}
                           #   writes bool into dest
    ADD         = auto()   # (dest: int, left: int, right: int)

    # --- control flow ---
    JUMP_IF_FALSE = auto() # (condition: int, offset: int)  — relative jump if register is falsy
    JUMP          = auto() # (offset: int,)                 — unconditional relative jump

    # --- higher-order / lambda ---
    MAP_CHARS   = auto()   # (dest: int, input_reg: int, lambda_reg: int)
                           #   apply lambda to every character; rejoin
    MAP_WORDS   = auto()   # (dest: int, input_reg: int, lambda_reg: int)
                           #   apply lambda to every whitespace-delimited word; rejoin
    CALL_LAMBDA = auto()   # (dest: int, lambda_reg: int, input_reg: int)
                           #   apply lambda to the full value in input_reg

    # --- meta ---
    LABEL  = auto()        # (name: str,)                   — logical label, not a real instruction
    HALT   = auto()        # (result: int,)                 — stop; result register holds output


# ---- instruction dataclass --------------------------------------------------


@dataclass(frozen=True)
class Instruction:
    """
    An immutable bytecode instruction.

    The `operands` tuple shape depends on the opcode:
        LOAD_CONST:    (dest_reg, value)
        LOAD_VAR:      (dest_reg, var_name)
        STORE_VAR:     (var_name, src_reg)
        MOV:           (dest_reg, src_reg)
        CALL_PLUGIN:   (dest_reg, plugin_name, arg_reg_list, kwarg_reg_dict)
        GET_LENGTH:    (dest_reg, src_reg)
        COMPARE:       (dest_reg, op_str, left_reg, right_reg)
        ADD:           (dest_reg, left_reg, right_reg)
        JUMP_IF_FALSE: (condition_reg, jump_offset)
        JUMP:          (jump_offset,)
        LABEL:         (label_name,)
        HALT:          (result_reg,)
    """

    opcode: Opcode
    operands: Tuple[Any, ...] = field(default_factory=tuple)

    def __repr__(self) -> str:
        return f"{self.opcode.name:<16} {self._operand_repr()}"

    def _operand_repr(self) -> str:
        parts: list[str] = []
        for op in self.operands:
            if isinstance(op, int):
                parts.append(f"R{op}" if _is_register_operand(self.opcode, op) else str(op))
            elif isinstance(op, list):
                parts.append(f"[{', '.join(f'R{r}' for r in op)}]")
            elif isinstance(op, dict):
                parts.append("{" + ", ".join(f"{k}=R{v}" for k, v in op.items()) + "}")
            elif hasattr(op, "param") and hasattr(op, "bytecode"):
                # LambdaObject — show inline disassembly at a glance
                parts.append(repr(op))
            else:
                parts.append(repr(op))
        return "  ".join(parts)


def _is_register_operand(opcode: Opcode, value: int) -> bool:
    """Heuristic: is this integer likely a register index vs. a literal?"""
    return opcode not in (Opcode.JUMP, Opcode.JUMP_IF_FALSE, Opcode.LOAD_CONST)


# ---- bytecode container -----------------------------------------------------


@dataclass
class Bytecode:
    """
    A compiled program: a sequence of Instructions plus metadata.
    Produced by the Compiler; consumed by the VirtualMachine.
    """

    instructions: List[Instruction] = field(default_factory=list)
    source: str = ""
    register_count: int = 0
    label_map: Dict[str, int] = field(default_factory=dict)  # label -> instruction index

    def __len__(self) -> int:
        return len(self.instructions)

    def __getitem__(self, index: int) -> Instruction:
        return self.instructions[index]

    def disassemble(self) -> str:
        """Human-readable disassembly listing."""
        lines: list[str] = [
            f"; STRATUM bytecode  registers={self.register_count}  instructions={len(self)}",
            f"; source: {self.source!r}",
            "",
        ]
        reverse_labels = {v: k for k, v in self.label_map.items()}
        for i, instr in enumerate(self.instructions):
            label = reverse_labels.get(i)
            if label:
                lines.append(f"{label}:")
            lines.append(f"  {i:04d}  {instr!r}")
        return "\n".join(lines)

    def to_list(self) -> list[dict]:
        return [
            {"opcode": instr.opcode.name, "operands": list(instr.operands)}
            for instr in self.instructions
        ]


# ---- lambda object ----------------------------------------------------------


@dataclass
class LambdaObject:
    """
    A first-class lambda value: the name of its single parameter and
    a standalone Bytecode that implements its body.

    Stored as a LOAD_CONST value; passed around in registers.
    Executed by the VM in a fresh child Frame (multi-frame call stack).
    """

    param: str
    bytecode: Bytecode

    # Identity-based equality — two separately compiled lambdas are never equal
    # even if they happen to do the same thing.
    def __eq__(self, other: object) -> bool:
        return self is other

    def __hash__(self) -> int:
        return id(self)

    def __repr__(self) -> str:
        return f"Lambda({self.param!r}, {len(self.bytecode)} instrs)"


# ---- builder ----------------------------------------------------------------


class BytecodeBuilder:
    """
    Fluent builder for Bytecode.
    Resolves forward label references when build() is called.

    Pattern: Builder.
    """

    def __init__(self, source: str = "") -> None:
        self._instructions: List[Instruction] = []
        self._labels: Dict[str, int] = {}
        self._pending_jumps: List[Tuple[int, str]] = []  # (instr_index, label_name)
        self._register_hwm = 0  # highest register index seen
        self._source = source

    def _track_reg(self, reg: int) -> int:
        if reg > self._register_hwm:
            self._register_hwm = reg
        return reg

    def emit(self, opcode: Opcode, *operands: Any) -> int:
        """Emit one instruction. Returns its index."""
        idx = len(self._instructions)
        self._instructions.append(Instruction(opcode=opcode, operands=tuple(operands)))
        # track register high-water mark
        for op in operands:
            if isinstance(op, int) and op >= 0 and op < 256:
                self._track_reg(op)
        return idx

    def emit_load_const(self, dest: int, value: Any) -> int:
        return self.emit(Opcode.LOAD_CONST, dest, value)

    def emit_load_var(self, dest: int, name: str) -> int:
        return self.emit(Opcode.LOAD_VAR, dest, name)

    def emit_store_var(self, name: str, src: int) -> int:
        return self.emit(Opcode.STORE_VAR, name, src)

    def emit_mov(self, dest: int, src: int) -> int:
        return self.emit(Opcode.MOV, dest, src)

    def emit_call_plugin(
        self, dest: int, name: str, arg_regs: List[int], kwarg_regs: Dict[str, int]
    ) -> int:
        return self.emit(Opcode.CALL_PLUGIN, dest, name, arg_regs, kwarg_regs)

    def emit_get_length(self, dest: int, src: int) -> int:
        return self.emit(Opcode.GET_LENGTH, dest, src)

    def emit_compare(self, dest: int, op: str, left: int, right: int) -> int:
        return self.emit(Opcode.COMPARE, dest, op, left, right)

    def emit_add(self, dest: int, left: int, right: int) -> int:
        return self.emit(Opcode.ADD, dest, left, right)

    def emit_jump(self, label: str) -> int:
        idx = self.emit(Opcode.JUMP, 0)  # placeholder offset
        self._pending_jumps.append((idx, label))
        return idx

    def emit_jump_if_false(self, condition: int, label: str) -> int:
        idx = self.emit(Opcode.JUMP_IF_FALSE, condition, 0)  # placeholder
        self._pending_jumps.append((idx, label))
        return idx

    def emit_halt(self, result_reg: int) -> int:
        return self.emit(Opcode.HALT, result_reg)

    def emit_map_chars(self, dest: int, input_reg: int, lambda_reg: int) -> int:
        return self.emit(Opcode.MAP_CHARS, dest, input_reg, lambda_reg)

    def emit_map_words(self, dest: int, input_reg: int, lambda_reg: int) -> int:
        return self.emit(Opcode.MAP_WORDS, dest, input_reg, lambda_reg)

    def emit_call_lambda(self, dest: int, lambda_reg: int, input_reg: int) -> int:
        return self.emit(Opcode.CALL_LAMBDA, dest, lambda_reg, input_reg)

    def label(self, name: str) -> None:
        """Mark the current position with a label."""
        self._labels[name] = len(self._instructions)
        self._instructions.append(Instruction(opcode=Opcode.LABEL, operands=(name,)))

    def current_index(self) -> int:
        return len(self._instructions)

    def build(self) -> Bytecode:
        """
        Strip LABEL pseudo-instructions, then patch jump targets.

        Two-pass:
        1. Strip LABELs, build raw→final index map and label_map (name→final index).
        2. Patch JUMP / JUMP_IF_FALSE instructions using final positions.
        """
        raw = list(self._instructions)

        # Pass 1: strip labels
        final: List[Instruction] = []
        label_map: Dict[str, int] = {}
        raw_to_final: Dict[int, int] = {}

        for raw_idx, instr in enumerate(raw):
            if instr.opcode == Opcode.LABEL:
                label_map[instr.operands[0]] = len(final)
            else:
                raw_to_final[raw_idx] = len(final)
                final.append(instr)

        # Pass 2: patch pending jumps using final-position label targets
        for raw_instr_idx, label_name in self._pending_jumps:
            final_instr_idx = raw_to_final.get(raw_instr_idx)
            if final_instr_idx is None:
                raise ValueError(
                    f"Jump at raw index {raw_instr_idx} has no final-instruction mapping"
                )
            target = label_map.get(label_name)
            if target is None:
                raise ValueError(f"Unresolved label: {label_name!r}")
            old = final[final_instr_idx]
            if old.opcode == Opcode.JUMP:
                final[final_instr_idx] = Instruction(
                    opcode=Opcode.JUMP, operands=(target,)
                )
            elif old.opcode == Opcode.JUMP_IF_FALSE:
                final[final_instr_idx] = Instruction(
                    opcode=Opcode.JUMP_IF_FALSE, operands=(old.operands[0], target)
                )

        return Bytecode(
            instructions=final,
            source=self._source,
            register_count=self._register_hwm + 1,
            label_map=label_map,
        )
