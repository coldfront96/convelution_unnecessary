"""
End-to-end integration tests. These exercise the full pipeline:
lex → parse → optimize → compile → execute.
"""

import pytest
from stratum.core.events import EventBus, EventStore
from stratum.core.config import load_config
from stratum.orchestrator import Orchestrator
from stratum.plugins.base import BuiltinPluginFactory
from stratum.plugins.registry import PluginRegistry


@pytest.fixture
def system():
    config = load_config()  # all defaults
    config.storage.in_memory = True  # don't write to disk in tests
    bus = EventBus()
    store = EventStore(db_path=":memory:")
    registry = PluginRegistry(event_bus=bus, event_store=store)
    registry.register_from_factory(BuiltinPluginFactory())
    orchestrator = Orchestrator(
        registry=registry, event_bus=bus, event_store=store, config=config
    )
    return orchestrator


def run(system, input_text: str, program: str) -> str:
    result = system.transform(input_text, program)
    assert result.is_ok(), f"Expected Ok, got Err: {result.unwrap_err()}"
    return result.unwrap()


def test_upper(system):
    assert run(system, "hello", "upper") == "HELLO"


def test_lower(system):
    assert run(system, "HELLO", "lower") == "hello"


def test_reverse(system):
    assert run(system, "hello", "reverse") == "olleh"


def test_rot13(system):
    assert run(system, "hello", "rot13") == "uryyb"
    assert run(system, "uryyb", "rot13") == "hello"  # idempotent


def test_pipeline(system):
    assert run(system, "hello world", "upper | reverse") == "DLROW OLLEH"


def test_three_stage_pipeline(system):
    result = run(system, "hello", "upper | reverse | rot13")
    assert result == "BYYRU"


def test_pad_left(system):
    result = run(system, "hi", "pad_left(width=5, char='.')")
    assert result == "...hi"


def test_pad_right(system):
    result = run(system, "hi", "pad_right(width=5, char='.')")
    assert result == "hi..."


def test_truncate(system):
    result = run(system, "hello world", "truncate(length=5, ellipsis='...')")
    assert result == "he..."


def test_append(system):
    assert run(system, "hello", "append(suffix='!')") == "hello!"


def test_prepend(system):
    assert run(system, "world", "prepend(prefix='hello ')") == "hello world"


def test_replace(system):
    assert run(system, "hello world", "replace(old='world', new='earth')") == "hello earth"


def test_title(system):
    assert run(system, "hello world", "title") == "Hello World"


def test_swapcase(system):
    assert run(system, "Hello", "swapcase") == "hELLO"


def test_base64_roundtrip(system):
    encoded = run(system, "hello world", "base64_encode")
    decoded = run(system, encoded, "base64_decode")
    assert decoded == "hello world"


def test_caesar_default(system):
    result = run(system, "abc", "caesar")
    assert result == "def"


def test_mirror(system):
    result = run(system, "abc", "mirror")
    assert result == "abccba"


def test_sort_words(system):
    result = run(system, "banana apple cherry", "sort")
    assert result == "apple banana cherry"


def test_unique_words(system):
    result = run(system, "a b a c b", "unique")
    assert result == "a b c"


def test_repeat(system):
    result = run(system, "ha", "repeat(times=3)")
    assert result == "hahaha"


def test_chunk(system):
    result = run(system, "abcdefgh", "chunk(size=3)")
    assert result == "abc def gh"


def test_conditional_true_branch(system):
    result = run(system, "hello world", "if length > 5 then upper else lower")
    assert result == "HELLO WORLD"


def test_conditional_false_branch(system):
    result = run(system, "hi", "if length > 5 then upper else lower")
    assert result == "hi"


def test_macro_expansion(system):
    result = run(system, "test", "macro shout = upper | append(suffix='!!!'); shout")
    assert result == "TEST!!!"


def test_let_binding(system):
    # length gives us the character count as a string; we test it doesn't crash
    result = run(system, "hello", "let n = length; upper")
    assert result == "HELLO"


def test_trim(system):
    assert run(system, "  hello  ", "trim") == "hello"


def test_word_count(system):
    assert run(system, "one two three", "word_count") == "3"


def test_char_count(system):
    assert run(system, "hello", "char_count") == "5"


def test_identity_is_noop(system):
    assert run(system, "hello", "identity") == "hello"


def test_identity_removed_from_pipeline(system):
    result = run(system, "hello", "identity | upper | identity")
    assert result == "HELLO"


def test_empty_program_returns_input(system):
    result = system.transform("hello", "")
    assert result.is_ok()
    assert result.unwrap() == "hello"


def test_error_unknown_plugin(system):
    result = system.transform("hello", "does_not_exist")
    assert result.is_err()


def test_error_lex_error(system):
    result = system.transform("hello", '"unterminated')
    assert result.is_err()
    assert "lex" in result.unwrap_err()


def test_disasm_returns_string(system):
    result = system.disassemble("upper | reverse")
    assert result.is_ok()
    assert "CALL_PLUGIN" in result.unwrap()


def test_join_and_split(system):
    result = run(system, "a,b,c", "split(sep=',') | join(sep=' ')")
    assert result == "a b c"
