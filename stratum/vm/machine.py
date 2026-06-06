"""
Register-based virtual machine.

Executes Bytecode by dispatching each Instruction to a handler method.
All transforms are delegated to the PluginRegistry — the VM has no
knowledge of what any named transform actually does.

Emits events to the EventBus for every significant state transition.

Pattern: State (execution state machine), Iterator (instruction stream),
         Command (instructions dispatched via opcode-to-handler map).
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable, Dict, Optional

from stratum.core.events import (
    EventBus,
    EventStore,
    InstructionExecuted,
    PluginCalled,
    TransformationFailed,
)
from stratum.core.result import Err, Ok, Result
from stratum.vm.frame import Frame
from stratum.vm.opcodes import Bytecode, Instruction, Opcode

logger = logging.getLogger(__name__)


class ExecutionError(Exception):
    pass


class VmState:
    """Enum-like execution state."""
    IDLE     = "IDLE"
    RUNNING  = "RUNNING"
    HALTED   = "HALTED"
    FAULTED  = "FAULTED"


class VirtualMachine:
    """
    The STRATUM register-based virtual machine.

    One VirtualMachine instance can run multiple programs sequentially
    (not concurrently). Each run creates a fresh Frame.
    """

    def __init__(
        self,
        plugin_registry: Any,  # PluginRegistry — late import to avoid circularity
        event_bus: EventBus,
        event_store: EventStore,
        register_count: int = 16,
        max_instructions: int = 100_000,
        emit_instruction_events: bool = False,
    ) -> None:
        self._registry = plugin_registry
        self._bus = event_bus
        self._store = event_store
        self._register_count = register_count
        self._max_instructions = max_instructions
        self._emit_instr_events = emit_instruction_events
        self._state = VmState.IDLE
        self._handlers: Dict[Opcode, Callable[[Instruction, Frame, str], None]] = self._build_dispatch_table()

    def _build_dispatch_table(self) -> Dict[Opcode, Callable[[Instruction, Frame, str], None]]:
        """Build the opcode → handler map. Pattern: Command dispatch table."""
        return {
            Opcode.LOAD_CONST:     self._op_load_const,
            Opcode.LOAD_VAR:       self._op_load_var,
            Opcode.STORE_VAR:      self._op_store_var,
            Opcode.MOV:            self._op_mov,
            Opcode.CALL_PLUGIN:    self._op_call_plugin,
            Opcode.GET_LENGTH:     self._op_get_length,
            Opcode.COMPARE:        self._op_compare,
            Opcode.ADD:            self._op_add,
            Opcode.JUMP_IF_FALSE:  self._op_jump_if_false,
            Opcode.JUMP:           self._op_jump,
            Opcode.HALT:           self._op_halt,
            Opcode.LABEL:          self._op_noop,
        }

    # ---- execution entry point ----------------------------------------------

    def execute(
        self, bytecode: Bytecode, input_text: str, session_id: str = ""
    ) -> Result[str, str]:
        """
        Execute a compiled program.
        Returns Ok(output_text) or Err(error_message).
        """
        if self._state == VmState.RUNNING:
            return Err("VM is already executing (not re-entrant)")

        frame = Frame(register_count=self._register_count)
        frame.store_register(0, input_text)  # R0 = input
        self._state = VmState.RUNNING
        result_value = input_text

        instructions_executed = 0

        try:
            while frame.ip < len(bytecode):
                if instructions_executed >= self._max_instructions:
                    raise ExecutionError(
                        f"Instruction limit ({self._max_instructions}) exceeded. "
                        "Your program may be infinitely looping."
                    )

                instr = bytecode[frame.ip]
                frame.ip += 1

                if self._emit_instr_events:
                    self._emit_instr_event(instr, frame, session_id)

                handler = self._handlers.get(instr.opcode)
                if handler is None:
                    raise ExecutionError(f"Unknown opcode: {instr.opcode}")

                try:
                    handler(instr, frame, session_id)
                except _HaltSignal as halt:
                    result_value = halt.value
                    break

                instructions_executed += 1

            self._state = VmState.HALTED
            return Ok(result_value)

        except ExecutionError as exc:
            self._state = VmState.FAULTED
            event = TransformationFailed(
                session_id=session_id,
                phase="vm",
                error_message=str(exc),
            )
            self._store.append(event)
            self._bus.publish(event)
            return Err(str(exc))

        except Exception as exc:
            self._state = VmState.FAULTED
            logger.exception("VM crashed during execution")
            return Err(f"Internal VM error: {exc}")

        finally:
            self._state = VmState.IDLE

    # ---- opcode handlers ----------------------------------------------------

    def _op_load_const(self, instr: Instruction, frame: Frame, session_id: str) -> None:
        dest, value = instr.operands[0], instr.operands[1]
        frame.store_register(dest, value)

    def _op_load_var(self, instr: Instruction, frame: Frame, session_id: str) -> None:
        dest, name = instr.operands[0], instr.operands[1]
        value = frame.load_variable(name)
        frame.store_register(dest, value)

    def _op_store_var(self, instr: Instruction, frame: Frame, session_id: str) -> None:
        name, src = instr.operands[0], instr.operands[1]
        value = frame.load_register(src)
        frame.store_variable(name, value)

    def _op_mov(self, instr: Instruction, frame: Frame, session_id: str) -> None:
        dest, src = instr.operands[0], instr.operands[1]
        frame.store_register(dest, frame.load_register(src))

    def _op_call_plugin(self, instr: Instruction, frame: Frame, session_id: str) -> None:
        dest: int       = instr.operands[0]
        name: str       = instr.operands[1]
        arg_regs: list  = instr.operands[2]
        kwarg_regs: dict = instr.operands[3]

        args = [frame.load_register(r) for r in arg_regs]
        kwargs = {k: frame.load_register(v) for k, v in kwarg_regs.items()}

        input_arg = args[0] if args else ""
        plugin_args = args[1:]

        t0 = time.monotonic_ns()
        result = self._registry.invoke(name, input_arg, *plugin_args, **kwargs)
        duration_us = (time.monotonic_ns() - t0) // 1000

        if result.is_err():
            raise ExecutionError(f"Plugin '{name}' failed: {result.unwrap_err()}")

        output = result.unwrap()
        frame.store_register(dest, output)

        event = PluginCalled(
            session_id=session_id,
            plugin_name=name,
            input_length=len(str(input_arg)),
            output_length=len(str(output)),
            duration_us=duration_us,
            succeeded=True,
        )
        self._store.append(event)
        self._bus.publish(event)

    def _op_get_length(self, instr: Instruction, frame: Frame, session_id: str) -> None:
        dest, src = instr.operands[0], instr.operands[1]
        val = frame.load_register(src)
        frame.store_register(dest, len(str(val)))

    def _op_compare(self, instr: Instruction, frame: Frame, session_id: str) -> None:
        dest: int  = instr.operands[0]
        op: str    = instr.operands[1]
        left: int  = instr.operands[2]
        right: int = instr.operands[3]

        lv = frame.load_register(left)
        rv = frame.load_register(right)

        # Numeric coercion: if one side is a string that looks like a number,
        # coerce it to match the type of the other side (e.g. length > 5).
        if isinstance(lv, str) and isinstance(rv, (int, float)):
            try:
                lv = type(rv)(lv)
            except (ValueError, TypeError):
                pass
        elif isinstance(rv, str) and isinstance(lv, (int, float)):
            try:
                rv = type(lv)(rv)
            except (ValueError, TypeError):
                pass

        try:
            result_bool = {
                "==": lv == rv,
                "!=": lv != rv,
                "<":  lv <  rv,
                "<=": lv <= rv,
                ">":  lv >  rv,
                ">=": lv >= rv,
            }[op]
        except KeyError:
            raise ExecutionError(f"Unknown comparison operator: {op!r}")
        except TypeError:
            result_bool = False

        frame.store_register(dest, int(result_bool))
        frame.set_last_compare(result_bool)

    def _op_add(self, instr: Instruction, frame: Frame, session_id: str) -> None:
        dest, left, right = instr.operands
        lv = frame.load_register(left)
        rv = frame.load_register(right)
        try:
            frame.store_register(dest, lv + rv)
        except TypeError:
            frame.store_register(dest, str(lv) + str(rv))

    def _op_jump_if_false(self, instr: Instruction, frame: Frame, session_id: str) -> None:
        condition_reg, target = instr.operands
        val = frame.load_register(condition_reg)
        if not val:
            frame.ip = target

    def _op_jump(self, instr: Instruction, frame: Frame, session_id: str) -> None:
        target = instr.operands[0]
        frame.ip = target

    def _op_halt(self, instr: Instruction, frame: Frame, session_id: str) -> None:
        result_reg = instr.operands[0]
        value = frame.load_register(result_reg)
        raise _HaltSignal(str(value))

    def _op_noop(self, instr: Instruction, frame: Frame, session_id: str) -> None:
        pass

    # ---- helper -------------------------------------------------------------

    def _emit_instr_event(
        self, instr: Instruction, frame: Frame, session_id: str
    ) -> None:
        snapshot = frame.snapshot()
        event = InstructionExecuted(
            session_id=session_id,
            opcode=instr.opcode.name,
            ip=frame.ip - 1,
            register_snapshot=snapshot.to_json(),
        )
        self._store.append(event)
        self._bus.publish(event)


class _HaltSignal(BaseException):
    """Used to break out of the execution loop cleanly."""
    def __init__(self, value: str) -> None:
        self.value = value
