"""
Pipeline Orchestrator — the Facade over the entire STRATUM system.

The Orchestrator is the only component allowed to call other layers directly.
Everything else communicates through the EventBus.

Sequence per transformation:
  1. Publish TransformationStarted
  2. Lex → Token list
  3. Parse → AST (Program)
  4. Optimize → optimized AST + PassResults
  5. Compile → Bytecode
  6. Execute → VM result
  7. Publish TransformationCompleted or TransformationFailed
  8. Return Result[str, str]

Pattern: Facade (single entry point to the whole system).
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Optional

from stratum.core.config import Config
from stratum.core.events import (
    AstProduced,
    BytecodeProduced,
    EventBus,
    EventStore,
    OptimizationApplied,
    TokensProduced,
    TransformationCompleted,
    TransformationFailed,
    TransformationStarted,
    TypeCheckCompleted,
)
from stratum.core.result import Err, Ok, Result
from stratum.lang.compiler import Compiler, CompileError
from stratum.lang.lexer import Lexer, LexError
from stratum.lang.optimizer import Optimizer
from stratum.lang.parser import Parser, ParseError
from stratum.lang.typechecker import TypeChecker
from stratum.plugins.registry import PluginRegistry
from stratum.vm.machine import VirtualMachine

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Coordinates the full lex → parse → optimize → compile → execute pipeline.
    The only component that knows about all subsystems.
    """

    def __init__(
        self,
        registry: PluginRegistry,
        event_bus: EventBus,
        event_store: EventStore,
        config: Config,
    ) -> None:
        self._registry = registry
        self._bus = event_bus
        self._store = event_store
        self._config = config

        self._optimizer = Optimizer(
            passes=config.optimizer.passes,
            max_iterations=config.optimizer.max_iterations,
            enabled=config.optimizer.enabled,
        )
        self._type_checker = TypeChecker()
        self._compiler = Compiler(max_registers=config.vm.register_count)
        self._vm = VirtualMachine(
            plugin_registry=registry,
            event_bus=event_bus,
            event_store=event_store,
            register_count=config.vm.register_count,
            max_instructions=config.vm.max_instructions,
            emit_instruction_events=config.vm.emit_instruction_events,
        )

    def transform(
        self, input_text: str, program_source: str, session_id: Optional[str] = None
    ) -> Result[str, str]:
        """
        Execute a full transformation. Returns Ok(output) or Err(message).
        """
        if session_id is None:
            session_id = str(uuid.uuid4())

        t_start = time.monotonic()

        # Step 1: announce start
        start_event = TransformationStarted(
            input_text=input_text,
            program_source=program_source,
            session_id=session_id,
        )
        self._store.append(start_event)
        self._bus.publish(start_event)

        # Step 2: lex
        try:
            lexer = Lexer(program_source)
            tokens = lexer.tokenize()
        except LexError as exc:
            return self._fail(session_id, "lex", str(exc))

        lex_event = TokensProduced(
            session_id=session_id,
            token_count=len(tokens),
            token_summary=str([t.type.name for t in tokens[:10]]),
        )
        self._store.append(lex_event)
        self._bus.publish(lex_event)

        # Step 3: parse
        try:
            parser = Parser(tokens)
            program = parser.parse()
        except ParseError as exc:
            return self._fail(session_id, "parse", str(exc))

        ast_event = AstProduced(
            session_id=session_id,
            node_count=program.node_count(),
            root_type=type(program).__name__,
        )
        self._store.append(ast_event)
        self._bus.publish(ast_event)

        # Step 4: optimize
        try:
            optimized, pass_results = self._optimizer.optimize(program)
        except Exception as exc:
            return self._fail(session_id, "optimize", str(exc))

        for pr in pass_results:
            if pr.changed:
                opt_event = OptimizationApplied(
                    session_id=session_id,
                    pass_name=pr.pass_name,
                    nodes_removed=pr.nodes_removed,
                    nodes_rewritten=pr.nodes_rewritten,
                )
                self._store.append(opt_event)
                self._bus.publish(opt_event)

        # Step 5: type-check (non-fatal: errors emit warnings, don't block execution)
        tc_result = self._type_checker.check(optimized)
        tc_event = TypeCheckCompleted(
            session_id=session_id,
            inferred_type=str(tc_result.inferred_type),
            error_count=len(tc_result.errors),
            warning_count=len(tc_result.warnings),
        )
        self._store.append(tc_event)
        self._bus.publish(tc_event)
        for diag in tc_result.diagnostics:
            logger.log(
                logging.WARNING if diag.is_warning() else logging.ERROR,
                "type check: %s",
                diag,
            )

        # Step 6: compile
        try:
            bytecode = self._compiler.compile(optimized, source=program_source)
        except CompileError as exc:
            return self._fail(session_id, "compile", str(exc))

        bc_event = BytecodeProduced(
            session_id=session_id,
            instruction_count=len(bytecode),
            register_count=bytecode.register_count,
        )
        self._store.append(bc_event)
        self._bus.publish(bc_event)

        # Step 7: execute
        result = self._vm.execute(bytecode, input_text, session_id=session_id)

        duration_ms = (time.monotonic() - t_start) * 1000

        if result.is_err():
            return self._fail(session_id, "vm", result.unwrap_err())

        output = result.unwrap()

        # Step 8: announce completion
        done_event = TransformationCompleted(
            session_id=session_id,
            input_text=input_text,
            output_text=output,
            duration_ms=duration_ms,
            instruction_count=len(bytecode),
        )
        self._store.append(done_event)
        self._bus.publish(done_event)

        logger.info(
            "transformation complete  session=%s  %.1fms  %d instructions",
            session_id[:8],
            duration_ms,
            len(bytecode),
        )

        return Ok(output)

    def _fail(self, session_id: str, phase: str, message: str) -> Result[str, str]:
        event = TransformationFailed(
            session_id=session_id,
            phase=phase,
            error_message=message,
        )
        self._store.append(event)
        self._bus.publish(event)
        logger.error("transformation failed  phase=%s  %s", phase, message)
        return Err(f"[{phase}] {message}")

    def disassemble(self, program_source: str) -> Result[str, str]:
        """Lex, parse, optimize, type-check, compile, return annotated disassembly."""
        try:
            tokens = Lexer(program_source).tokenize()
            program = Parser(tokens).parse()
            optimized, _ = self._optimizer.optimize(program)
            tc_result = self._type_checker.check(optimized)
            bytecode = self._compiler.compile(optimized, source=program_source)
            header = f"; type: {tc_result.inferred_type}"
            if tc_result.diagnostics:
                header += "\n" + "\n".join(f"; {d}" for d in tc_result.diagnostics)
            return Ok(header + "\n" + bytecode.disassemble())
        except (LexError, ParseError, CompileError) as exc:
            return Err(str(exc))

    def typecheck(self, program_source: str) -> Result[str, str]:
        """Run only the type checker and return a human-readable report."""
        try:
            tokens = Lexer(program_source).tokenize()
            program = Parser(tokens).parse()
            optimized, _ = self._optimizer.optimize(program)
            tc_result = self._type_checker.check(optimized)
            return Ok(tc_result.summary())
        except (LexError, ParseError) as exc:
            return Err(str(exc))
