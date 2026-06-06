"""
Lexer for HQL (History Query Language).

HQL is a mini SQL-like language for querying transformation history:

    SELECT *
    SELECT input, output, duration_ms
    WHERE input CONTAINS "hello"
    WHERE duration_ms > 50
    WHERE succeeded = true
    ORDER BY duration_ms DESC
    LIMIT 10

Full example:
    SELECT input, output WHERE input CONTAINS "hi" AND duration_ms > 10 ORDER BY duration_ms DESC LIMIT 5

This is a *separate* lexer from the STL lexer — different token set,
different keywords, same hand-written approach.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import List


class QueryTokenType(Enum):
    # keywords
    KW_SELECT   = auto()
    KW_WHERE    = auto()
    KW_ORDER    = auto()
    KW_BY       = auto()
    KW_LIMIT    = auto()
    KW_AND      = auto()
    KW_OR       = auto()
    KW_NOT      = auto()
    KW_CONTAINS = auto()
    KW_STARTS   = auto()
    KW_ENDS     = auto()
    KW_WITH     = auto()
    KW_ASC      = auto()
    KW_DESC     = auto()
    KW_TRUE     = auto()
    KW_FALSE    = auto()
    KW_NULL     = auto()

    # identifiers and literals
    IDENT   = auto()
    STRING  = auto()
    NUMBER  = auto()
    STAR    = auto()   # *

    # operators
    EQ   = auto()   # =
    NEQ  = auto()   # !=
    LT   = auto()   # <
    LTE  = auto()   # <=
    GT   = auto()   # >
    GTE  = auto()   # >=

    # punctuation
    COMMA  = auto()
    EOF    = auto()


_KEYWORDS: dict[str, QueryTokenType] = {
    "select":   QueryTokenType.KW_SELECT,
    "where":    QueryTokenType.KW_WHERE,
    "order":    QueryTokenType.KW_ORDER,
    "by":       QueryTokenType.KW_BY,
    "limit":    QueryTokenType.KW_LIMIT,
    "and":      QueryTokenType.KW_AND,
    "or":       QueryTokenType.KW_OR,
    "not":      QueryTokenType.KW_NOT,
    "contains": QueryTokenType.KW_CONTAINS,
    "starts":   QueryTokenType.KW_STARTS,
    "ends":     QueryTokenType.KW_ENDS,
    "with":     QueryTokenType.KW_WITH,
    "asc":      QueryTokenType.KW_ASC,
    "desc":     QueryTokenType.KW_DESC,
    "true":     QueryTokenType.KW_TRUE,
    "false":    QueryTokenType.KW_FALSE,
    "null":     QueryTokenType.KW_NULL,
}


@dataclass(frozen=True)
class QueryToken:
    type: QueryTokenType
    value: str
    pos: int

    def __repr__(self) -> str:
        return f"QTok({self.type.name}, {self.value!r})"


class QueryLexError(Exception):
    def __init__(self, msg: str, pos: int):
        super().__init__(f"{msg} at position {pos}")
        self.pos = pos


class QueryLexer:
    """Hand-written lexer for HQL. Same philosophy as the STL lexer."""

    def __init__(self, source: str) -> None:
        self._source = source
        self._pos = 0
        self._tokens: List[QueryToken] = []

    def tokenize(self) -> List[QueryToken]:
        while not self._at_end():
            self._skip_whitespace()
            if self._at_end():
                break
            self._scan_one()
        self._tokens.append(QueryToken(QueryTokenType.EOF, "", self._pos))
        return self._tokens

    def _scan_one(self) -> None:
        start = self._pos
        ch = self._advance()

        if ch == "*":
            self._add(QueryTokenType.STAR, "*", start)
        elif ch == ",":
            self._add(QueryTokenType.COMMA, ",", start)
        elif ch == "=":
            self._add(QueryTokenType.EQ, "=", start)
        elif ch == "!":
            if self._peek() == "=":
                self._advance()
                self._add(QueryTokenType.NEQ, "!=", start)
            else:
                raise QueryLexError(f"Unexpected '!'", start)
        elif ch == "<":
            if self._peek() == "=":
                self._advance()
                self._add(QueryTokenType.LTE, "<=", start)
            else:
                self._add(QueryTokenType.LT, "<", start)
        elif ch == ">":
            if self._peek() == "=":
                self._advance()
                self._add(QueryTokenType.GTE, ">=", start)
            else:
                self._add(QueryTokenType.GT, ">", start)
        elif ch in ('"', "'"):
            self._scan_string(ch, start)
        elif ch.isdigit() or (ch == "-" and self._peek().isdigit()):
            self._scan_number(ch, start)
        elif ch.isalpha() or ch == "_":
            self._scan_ident(ch, start)
        else:
            raise QueryLexError(f"Unexpected character {ch!r}", start)

    def _scan_string(self, quote: str, start: int) -> None:
        buf: list[str] = []
        while not self._at_end() and self._peek() != quote:
            ch = self._advance()
            if ch == "\\" and not self._at_end():
                esc = self._advance()
                buf.append({"n": "\n", "t": "\t", "\\": "\\", "'": "'", '"': '"'}.get(esc, esc))
            else:
                buf.append(ch)
        if self._at_end():
            raise QueryLexError("Unterminated string", start)
        self._advance()
        self._add(QueryTokenType.STRING, "".join(buf), start)

    def _scan_number(self, first: str, start: int) -> None:
        buf = [first]
        while not self._at_end() and (self._peek().isdigit() or self._peek() == "."):
            buf.append(self._advance())
        self._add(QueryTokenType.NUMBER, "".join(buf), start)

    def _scan_ident(self, first: str, start: int) -> None:
        buf = [first]
        while not self._at_end() and (self._peek().isalnum() or self._peek() == "_"):
            buf.append(self._advance())
        word = "".join(buf)
        tok_type = _KEYWORDS.get(word.lower(), QueryTokenType.IDENT)
        self._add(tok_type, word, start)

    def _skip_whitespace(self) -> None:
        while not self._at_end() and self._peek() in (" ", "\t", "\n", "\r"):
            self._advance()

    def _at_end(self) -> bool:
        return self._pos >= len(self._source)

    def _advance(self) -> str:
        ch = self._source[self._pos]
        self._pos += 1
        return ch

    def _peek(self) -> str:
        if self._at_end():
            return "\0"
        return self._source[self._pos]

    def _add(self, ttype: QueryTokenType, value: str, pos: int) -> None:
        self._tokens.append(QueryToken(ttype, value, pos))
