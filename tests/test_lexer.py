"""Tests for the STL lexer."""

import pytest
from stratum.lang.lexer import Lexer, LexError, TokenType


def tokens(source: str):
    return [(t.type, t.value) for t in Lexer(source).tokenize() if t.type != TokenType.EOF]


def test_simple_ident():
    toks = tokens("upper")
    assert toks == [(TokenType.IDENT, "upper")]


def test_pipe():
    toks = tokens("upper | lower")
    assert toks == [
        (TokenType.IDENT, "upper"),
        (TokenType.PIPE, "|"),
        (TokenType.IDENT, "lower"),
    ]


def test_keywords():
    toks = tokens("if then else let macro true false")
    expected_types = [
        TokenType.KW_IF, TokenType.KW_THEN, TokenType.KW_ELSE,
        TokenType.KW_LET, TokenType.KW_MACRO, TokenType.KW_TRUE, TokenType.KW_FALSE,
    ]
    assert [t for t, _ in toks] == expected_types


def test_string_double_quote():
    toks = tokens('"hello world"')
    assert toks == [(TokenType.STRING, "hello world")]


def test_string_single_quote():
    toks = tokens("'hello'")
    assert toks == [(TokenType.STRING, "hello")]


def test_string_escape():
    toks = tokens(r'"hello\nworld"')
    assert toks == [(TokenType.STRING, "hello\nworld")]


def test_number_integer():
    toks = tokens("42")
    assert toks == [(TokenType.NUMBER, "42")]


def test_number_float():
    toks = tokens("3.14")
    assert toks == [(TokenType.NUMBER, "3.14")]


def test_comparison_ops():
    for src, expected in [
        ("==", TokenType.EQEQ), ("!=", TokenType.NEQ),
        ("<", TokenType.LT), ("<=", TokenType.LTE),
        (">", TokenType.GT), (">=", TokenType.GTE),
    ]:
        toks = tokens(src)
        assert toks[0][0] == expected, f"failed for {src!r}"


def test_arrow():
    toks = tokens("x => upper")
    assert toks[1] == (TokenType.ARROW, "=>")


def test_line_comment():
    toks = tokens("upper -- this is a comment\n| lower")
    assert toks == [
        (TokenType.IDENT, "upper"),
        (TokenType.PIPE, "|"),
        (TokenType.IDENT, "lower"),
    ]


def test_unterminated_string_raises():
    with pytest.raises(LexError):
        Lexer('"unterminated').tokenize()


def test_full_call():
    toks = tokens("pad_left(width=20, char='.')")
    types = [t for t, _ in toks]
    assert types[0] == TokenType.IDENT
    assert types[1] == TokenType.LPAREN
    assert types[-1] == TokenType.RPAREN
