"""Tests for the STL parser."""

import pytest
from stratum.lang.lexer import Lexer
from stratum.lang.parser import Parser, ParseError
from stratum.lang.ast_nodes import (
    BinaryOp, Call, Conditional, Identifier, LetBinding,
    MacroDef, NumberLiteral, Pipeline, Program, StringLiteral,
)


def parse(source: str) -> Program:
    tokens = Lexer(source).tokenize()
    return Parser(tokens).parse()


def test_simple_ident():
    p = parse("upper")
    assert isinstance(p.body, Identifier)
    assert p.body.name == "upper"


def test_pipeline():
    p = parse("upper | reverse")
    assert isinstance(p.body, Pipeline)
    assert len(p.body.stages) == 2


def test_call_no_args():
    p = parse("upper()")
    assert isinstance(p.body, Call)
    assert p.body.name == "upper"
    assert p.body.args == []
    assert p.body.kwargs == {}


def test_call_with_kwarg():
    p = parse("pad_left(width=20)")
    assert isinstance(p.body, Call)
    assert "width" in p.body.kwargs
    assert isinstance(p.body.kwargs["width"], NumberLiteral)
    assert p.body.kwargs["width"].value == 20


def test_conditional():
    p = parse("if length > 5 then upper else lower")
    assert isinstance(p.body, Conditional)
    assert isinstance(p.body.condition, BinaryOp)
    assert p.body.condition.op == ">"


def test_let_binding():
    p = parse("let x = length")
    assert isinstance(p.body, LetBinding)
    assert p.body.name == "x"


def test_let_with_body():
    p = parse("let x = length; upper")
    assert isinstance(p.body, LetBinding)
    assert p.body.body is not None


def test_macro_def():
    p = parse("macro shout = upper\nupper")
    assert len(p.macros) == 1
    assert p.macros[0].name == "shout"


def test_macro_with_semicolon_separator():
    p = parse("macro shout = upper; shout")
    assert len(p.macros) == 1
    assert isinstance(p.body, Identifier)
    assert p.body.name == "shout"


def test_string_literal():
    p = parse("append(suffix='!!!')")
    assert isinstance(p.body, Call)
    val = p.body.kwargs["suffix"]
    assert isinstance(val, StringLiteral)
    assert val.value == "!!!"


def test_number_literal_int():
    p = parse("repeat(times=3)")
    val = p.body.kwargs["times"]
    assert isinstance(val, NumberLiteral)
    assert val.value == 3


def test_nested_pipeline():
    p = parse("upper | reverse | rot13")
    assert isinstance(p.body, Pipeline)
    assert len(p.body.stages) == 3


def test_empty_program():
    p = parse("")
    assert p.body is None
    assert p.macros == []


def test_parse_error_raises():
    with pytest.raises(ParseError):
        parse("if upper")  # missing then/else
