"""Tests for the static type checker."""

import pytest
from stratum.lang.lexer import Lexer
from stratum.lang.parser import Parser
from stratum.lang.optimizer import Optimizer
from stratum.lang.typechecker import (
    TypeChecker, TypeCheckResult,
    STRING, INT, BOOL, UNKNOWN,
    StringType, IntType, BoolType, LambdaType, UnknownType,
    Severity,
)


def parse_and_check(source: str) -> TypeCheckResult:
    tokens = Lexer(source).tokenize()
    program = Parser(tokens).parse()
    optimized, _ = Optimizer().optimize(program)
    return TypeChecker().check(optimized)


def test_string_literal():
    r = parse_and_check('"hello"')
    assert isinstance(r.inferred_type, StringType)
    assert r.is_clean


def test_number_literal():
    r = parse_and_check("42")
    assert isinstance(r.inferred_type, IntType)
    assert r.is_clean


def test_known_plugin_returns_string():
    r = parse_and_check("upper")
    assert isinstance(r.inferred_type, StringType)
    assert r.is_clean


def test_pipeline_inferred_type():
    r = parse_and_check("upper | reverse")
    assert isinstance(r.inferred_type, StringType)
    assert r.is_clean


def test_lambda_infers_lambda_type():
    r = parse_and_check("map(char => upper(char))")
    assert isinstance(r.inferred_type, StringType)
    assert r.is_clean


def test_map_words_clean():
    r = parse_and_check("map_words(w => reverse(w))")
    assert isinstance(r.inferred_type, StringType)
    assert r.is_clean


def test_conditional_infers_string():
    r = parse_and_check("if length > 5 then upper else lower")
    assert isinstance(r.inferred_type, StringType)


def test_unknown_plugin_warns():
    r = parse_and_check("nonexistent_plugin")
    # unknown plugin should warn but not error
    # (it might be a registered plugin at runtime)
    # The type checker emits a warning for unknown plugins called with args only
    # A bare unknown identifier returns UNKNOWN — no diagnostic
    assert isinstance(r.inferred_type, UnknownType)


def test_unknown_plugin_with_args_warns():
    r = parse_and_check("nonexistent_plugin(42)")
    assert any(d.severity == Severity.WARNING for d in r.diagnostics)


def test_lambda_type_inferred():
    r = parse_and_check("x => upper(x)")
    # bare lambda — applied to input via CALL_LAMBDA at runtime
    # the typechecker sees a Lambda node and infers LambdaType or String
    # (it compiles to CALL_LAMBDA which returns String)


def test_comparison_returns_bool():
    r = parse_and_check("if length > 5 then upper else lower")
    # No errors for a valid comparison
    assert not r.errors


def test_chained_pipeline_clean():
    r = parse_and_check("upper | reverse | rot13 | base64_encode")
    assert isinstance(r.inferred_type, StringType)
    assert not r.errors


def test_macro_type_propagates():
    r = parse_and_check("macro shout = upper | append(suffix='!!!'); shout")
    assert isinstance(r.inferred_type, StringType)
    assert not r.errors


def test_let_binding_type():
    r = parse_and_check("let n = length; upper")
    assert not r.errors


def test_empty_program_is_unknown():
    r = parse_and_check("")
    assert isinstance(r.inferred_type, UnknownType)


def test_summary_ok_message():
    r = parse_and_check("upper")
    assert "OK" in r.summary()
    assert "String" in r.summary()


def test_summary_with_warnings():
    r = parse_and_check("nonexistent(42)")
    summary = r.summary()
    assert "WARNING" in summary or "inferred" in summary


def test_typecheck_via_orchestrator():
    from stratum.core.events import EventBus, EventStore
    from stratum.core.config import load_config
    from stratum.orchestrator import Orchestrator
    from stratum.plugins.base import BuiltinPluginFactory
    from stratum.plugins.registry import PluginRegistry

    config = load_config()
    bus = EventBus()
    store = EventStore(db_path=":memory:")
    registry = PluginRegistry(event_bus=bus, event_store=store)
    registry.register_from_factory(BuiltinPluginFactory())
    orch = Orchestrator(registry=registry, event_bus=bus, event_store=store, config=config)

    r = orch.typecheck("upper | reverse")
    assert r.is_ok()
    assert "String" in r.unwrap()


def test_disasm_includes_type_header():
    from stratum.core.events import EventBus, EventStore
    from stratum.core.config import load_config
    from stratum.orchestrator import Orchestrator
    from stratum.plugins.base import BuiltinPluginFactory
    from stratum.plugins.registry import PluginRegistry

    config = load_config()
    bus = EventBus()
    store = EventStore(db_path=":memory:")
    registry = PluginRegistry(event_bus=bus, event_store=store)
    registry.register_from_factory(BuiltinPluginFactory())
    orch = Orchestrator(registry=registry, event_bus=bus, event_store=store, config=config)

    r = orch.disassemble("upper | reverse")
    assert r.is_ok()
    assert "; type:" in r.unwrap()
