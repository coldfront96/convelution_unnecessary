"""
Execution frame for the virtual machine.

A Frame represents one level of the call stack.
It holds the register file, variable bindings, and instruction pointer.

Pattern: Memento — Frame snapshots can be taken and restored.
"""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Dict, Optional


class RegisterError(Exception):
    pass


@dataclass
class FrameSnapshot:
    """Immutable snapshot of a frame at a point in time (Memento pattern)."""
    ip: int
    registers: Dict[int, Any]
    variables: Dict[str, Any]
    flags: Dict[str, bool]

    def to_json(self) -> str:
        safe_regs = {str(k): str(v)[:64] for k, v in self.registers.items()}
        return json.dumps({
            "ip": self.ip,
            "registers": safe_regs,
            "variables": {k: str(v)[:64] for k, v in self.variables.items()},
            "flags": self.flags,
        })


class Frame:
    """
    Single execution frame.

    Registers are indexed by integer (0..N-1).
    Variables are indexed by name.
    Flags hold the result of the last COMPARE instruction.

    The frame does not know about the instruction stream —
    that lives in the VirtualMachine.
    """

    def __init__(self, register_count: int = 16) -> None:
        self._register_count = register_count
        self._registers: Dict[int, Any] = {}
        self._variables: Dict[str, Any] = {}
        self._flags: Dict[str, bool] = {"zero": False, "last_compare": False}
        self.ip: int = 0
        self.call_depth: int = 0

    # ---- register access ----------------------------------------------------

    def load_register(self, index: int) -> Any:
        self._check_register(index)
        return self._registers.get(index, "")

    def store_register(self, index: int, value: Any) -> None:
        self._check_register(index)
        self._registers[index] = value

    def _check_register(self, index: int) -> None:
        if not (0 <= index < self._register_count):
            raise RegisterError(
                f"Register R{index} out of range (0..{self._register_count - 1})"
            )

    # ---- variable access ----------------------------------------------------

    def load_variable(self, name: str) -> Any:
        if name not in self._variables:
            raise RegisterError(f"Undefined variable: {name!r}")
        return self._variables[name]

    def store_variable(self, name: str, value: Any) -> None:
        self._variables[name] = value

    def has_variable(self, name: str) -> bool:
        return name in self._variables

    # ---- flags --------------------------------------------------------------

    def set_flag(self, name: str, value: bool) -> None:
        self._flags[name] = value

    def get_flag(self, name: str) -> bool:
        return self._flags.get(name, False)

    def set_last_compare(self, result: bool) -> None:
        self._flags["last_compare"] = result
        self._flags["zero"] = not result

    def last_compare_result(self) -> bool:
        return self._flags.get("last_compare", False)

    # ---- register state introspection ---------------------------------------

    def registers_in_use(self) -> Dict[int, Any]:
        return dict(self._registers)

    def all_variables(self) -> Dict[str, Any]:
        return dict(self._variables)

    # ---- Memento support ----------------------------------------------------

    def snapshot(self) -> FrameSnapshot:
        return FrameSnapshot(
            ip=self.ip,
            registers=deepcopy(self._registers),
            variables=deepcopy(self._variables),
            flags=dict(self._flags),
        )

    def restore(self, snapshot: FrameSnapshot) -> None:
        self.ip = snapshot.ip
        self._registers = deepcopy(snapshot.registers)
        self._variables = deepcopy(snapshot.variables)
        self._flags = dict(snapshot.flags)

    def __repr__(self) -> str:
        reg_str = ", ".join(
            f"R{k}={str(v)[:16]!r}" for k, v in sorted(self._registers.items())
        )
        return f"Frame(ip={self.ip}, [{reg_str}], vars={list(self._variables.keys())})"
