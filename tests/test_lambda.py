"""
Tests for lambda expressions, map_chars, map_words, and the multi-frame VM.
"""

import pytest
from stratum.core.events import EventBus, EventStore
from stratum.core.config import load_config
from stratum.orchestrator import Orchestrator
from stratum.plugins.base import BuiltinPluginFactory
from stratum.plugins.registry import PluginRegistry


@pytest.fixture
def system():
    config = load_config()
    bus = EventBus()
    store = EventStore(db_path=":memory:")
    registry = PluginRegistry(event_bus=bus, event_store=store)
    registry.register_from_factory(BuiltinPluginFactory())
    return Orchestrator(registry=registry, event_bus=bus, event_store=store, config=config)


def run(system, input_text: str, program: str) -> str:
    result = system.transform(input_text, program)
    assert result.is_ok(), f"Expected Ok, got: {result.unwrap_err()}"
    return result.unwrap()


# ---- map_chars --------------------------------------------------------------

def test_map_chars_identity(system):
    result = run(system, "hello", "map(char => char)")
    assert result == "hello"


def test_map_chars_upper(system):
    result = run(system, "hello", "map(char => upper(char))")
    assert result == "HELLO"


def test_map_chars_rot13(system):
    result = run(system, "hello", "map(char => rot13(char))")
    assert result == "uryyb"


def test_map_chars_alias(system):
    # map_chars is an alias for map
    result = run(system, "hi", "map_chars(char => upper(char))")
    assert result == "HI"


def test_map_chars_preserves_spaces(system):
    result = run(system, "hello world", "map(char => upper(char))")
    assert result == "HELLO WORLD"


def test_map_chars_empty_string(system):
    result = run(system, "", "map(char => upper(char))")
    assert result == ""


def test_map_chars_pipeline_body(system):
    # Lambda body can itself be a pipeline
    result = run(system, "hello", "map(c => upper(c) | rot13(c))")
    # upper('h') = 'H', rot13('H') = 'U', etc.
    assert result == run(system, "hello", "upper | rot13")


def test_map_chars_with_pipeline_after(system):
    # Lambda can be followed by more pipeline stages
    result = run(system, "hello", "map(char => upper(char)) | reverse")
    assert result == "OLLEH"


# ---- map_words --------------------------------------------------------------

def test_map_words_upper(system):
    result = run(system, "hello world foo", "map_words(word => upper(word))")
    assert result == "HELLO WORLD FOO"


def test_map_words_reverse(system):
    result = run(system, "hello world", "map_words(word => reverse(word))")
    assert result == "olleh dlrow"


def test_map_words_single_word(system):
    result = run(system, "hello", "map_words(w => upper(w))")
    assert result == "HELLO"


def test_map_words_empty(system):
    result = run(system, "", "map_words(w => upper(w))")
    assert result == ""


def test_map_words_with_pipeline_before(system):
    result = run(system, "hello world", "upper | map_words(w => reverse(w))")
    assert result == "OLLEH DLROW"


# ---- bare lambda as program -------------------------------------------------

def test_bare_lambda_as_program(system):
    result = run(system, "hello", "x => upper(x)")
    assert result == "HELLO"


# ---- disassembly includes lambda info ---------------------------------------

def test_disasm_shows_map(system):
    result = system.disassemble("map(char => upper(char))")
    assert result.is_ok()
    asm = result.unwrap()
    assert "MAP_CHARS" in asm
    assert "Lambda" in asm


def test_disasm_shows_map_words(system):
    result = system.disassemble("map_words(w => reverse(w))")
    assert result.is_ok()
    assert "MAP_WORDS" in result.unwrap()


# ---- multi-frame call stack depth -------------------------------------------

def test_nested_map_chars(system):
    # map inside map_words — verifies multiple lambda frames can coexist
    # Each word is uppercased character-by-character
    result = run(system, "hello world", "map_words(w => map(c => upper(c)))")
    assert result == "HELLO WORLD"


def test_frame_stack_isolation(system):
    # Transforms in one lambda frame must not bleed into another
    r1 = run(system, "abc", "map(x => rot13(x))")
    r2 = run(system, "abc", "map(x => upper(x))")
    assert r1 == "nop"
    assert r2 == "ABC"
    # Running them sequentially must produce the same results
    assert run(system, "abc", "map(x => rot13(x))") == "nop"
    assert run(system, "abc", "map(x => upper(x))") == "ABC"
