"""Register-based virtual machine that executes STL bytecode."""

from stratum.vm.opcodes import Opcode, Instruction
from stratum.vm.frame import Frame
from stratum.vm.machine import VirtualMachine

__all__ = ["Opcode", "Instruction", "Frame", "VirtualMachine"]
