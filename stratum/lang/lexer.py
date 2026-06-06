"""
Hand-written lexer for STL (Stratum Transformation Language).

Converts a source string into a flat list of Tokens.
No regex. No split(). Character-by-character.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import List, Optional


class TokenType(Enum):
    # literals
    IDENT   = auto()
    STRING  = auto()
    NUMBER  = auto()

    # punctuation
    PIPE    = auto()   # |
    LPAREN  = auto()   # (
    RPAREN  = auto()   # )
    COMMA   = auto()   # ,
    EQ      = auto()   # =
    SEMICOL = auto()   # ;
    ARROW   = auto()   # =>
    COLON   = auto()   # :

    # comparison operators
    EQEQ    = auto()   # ==
    NEQ     = auto()   # !=
    LT      = auto()   # <
    LTE     = auto()   # <=
    GT      = auto()   # >
    GTE     = auto()   # >=

    # arithmetic
    PLUS    = auto()   # +
    MINUS   = auto()   # -
    STAR    = auto()   # *

    # keywords
    KW_IF     = auto()
    KW_THEN   = auto()
    KW_ELSE   = auto()
    KW_LET    = auto()
    KW_MACRO  = auto()
    KW_TRUE   = auto()
    KW_FALSE  = auto()

    EOF     = auto()


_KEYWORDS: dict[str, TokenType] = {
    "if":    TokenType.KW_IF,
    "then":  TokenType.KW_THEN,
    "else":  TokenType.KW_ELSE,
    "let":   TokenType.KW_LET,
    "macro": TokenType.KW_MACRO,
    "true":  TokenType.KW_TRUE,
    "false": TokenType.KW_FALSE,
}


@dataclass(frozen=True)
class Token:
    type: TokenType
    value: str
    line: int
    col: int

    def __repr__(self) -> str:
        return f"Token({self.type.name}, {self.value!r}, {self.line}:{self.col})"


class LexError(Exception):
    def __init__(self, message: str, line: int, col: int) -> None:
        super().__init__(f"{message} at line {line}, col {col}")
        self.line = line
        self.col = col


class Lexer:
    """
    Single-pass lexer. Call tokenize() to get the full token list.

    The lexer maintains a cursor position and advances through the source
    character by character. Multi-character tokens are handled by peeking
    at the next character.
    """

    def __init__(self, source: str) -> None:
        self._source = source
        self._pos = 0
        self._line = 1
        self._col = 1
        self._tokens: List[Token] = []

    def tokenize(self) -> List[Token]:
        while not self._at_end():
            self._skip_whitespace_and_comments()
            if self._at_end():
                break
            self._scan_one()
        self._tokens.append(Token(TokenType.EOF, "", self._line, self._col))
        return self._tokens

    # ---- scanning -----------------------------------------------------------

    def _scan_one(self) -> None:
        start_line = self._line
        start_col = self._col
        ch = self._advance()

        if ch == "|":
            self._add(TokenType.PIPE, "|", start_line, start_col)
        elif ch == "(":
            self._add(TokenType.LPAREN, "(", start_line, start_col)
        elif ch == ")":
            self._add(TokenType.RPAREN, ")", start_line, start_col)
        elif ch == ",":
            self._add(TokenType.COMMA, ",", start_line, start_col)
        elif ch == ";":
            self._add(TokenType.SEMICOL, ";", start_line, start_col)
        elif ch == "+":
            self._add(TokenType.PLUS, "+", start_line, start_col)
        elif ch == "-":
            self._add(TokenType.MINUS, "-", start_line, start_col)
        elif ch == "*":
            self._add(TokenType.STAR, "*", start_line, start_col)
        elif ch == ":":
            self._add(TokenType.COLON, ":", start_line, start_col)
        elif ch == "=":
            if self._peek() == "=":
                self._advance()
                self._add(TokenType.EQEQ, "==", start_line, start_col)
            elif self._peek() == ">":
                self._advance()
                self._add(TokenType.ARROW, "=>", start_line, start_col)
            else:
                self._add(TokenType.EQ, "=", start_line, start_col)
        elif ch == "!":
            if self._peek() == "=":
                self._advance()
                self._add(TokenType.NEQ, "!=", start_line, start_col)
            else:
                raise LexError(f"Unexpected character '!'", start_line, start_col)
        elif ch == "<":
            if self._peek() == "=":
                self._advance()
                self._add(TokenType.LTE, "<=", start_line, start_col)
            else:
                self._add(TokenType.LT, "<", start_line, start_col)
        elif ch == ">":
            if self._peek() == "=":
                self._advance()
                self._add(TokenType.GTE, ">=", start_line, start_col)
            else:
                self._add(TokenType.GT, ">", start_line, start_col)
        elif ch in ('"', "'"):
            self._scan_string(ch, start_line, start_col)
        elif ch.isdigit():
            self._scan_number(ch, start_line, start_col)
        elif ch.isalpha() or ch == "_":
            self._scan_ident(ch, start_line, start_col)
        else:
            raise LexError(f"Unexpected character {ch!r}", start_line, start_col)

    def _scan_string(self, quote: str, line: int, col: int) -> None:
        buf: list[str] = []
        while not self._at_end() and self._peek() != quote:
            ch = self._advance()
            if ch == "\\":
                if self._at_end():
                    raise LexError("Unterminated escape sequence", self._line, self._col)
                esc = self._advance()
                buf.append({"n": "\n", "t": "\t", "r": "\r", "\\": "\\", "'": "'", '"': '"'}.get(esc, esc))
            else:
                buf.append(ch)
        if self._at_end():
            raise LexError("Unterminated string literal", line, col)
        self._advance()  # closing quote
        self._add(TokenType.STRING, "".join(buf), line, col)

    def _scan_number(self, first: str, line: int, col: int) -> None:
        buf = [first]
        while not self._at_end() and (self._peek().isdigit() or self._peek() == "."):
            buf.append(self._advance())
        self._add(TokenType.NUMBER, "".join(buf), line, col)

    def _scan_ident(self, first: str, line: int, col: int) -> None:
        buf = [first]
        while not self._at_end() and (self._peek().isalnum() or self._peek() in ("_",)):
            buf.append(self._advance())
        word = "".join(buf)
        tok_type = _KEYWORDS.get(word, TokenType.IDENT)
        self._add(tok_type, word, line, col)

    # ---- helpers ------------------------------------------------------------

    def _skip_whitespace_and_comments(self) -> None:
        while not self._at_end():
            ch = self._peek()
            if ch in (" ", "\t", "\r"):
                self._advance()
            elif ch == "\n":
                self._advance()
                self._line += 1
                self._col = 1
            elif ch == "-" and self._peek2() == "-":
                # line comment: -- ...
                while not self._at_end() and self._peek() != "\n":
                    self._advance()
            else:
                break

    def _at_end(self) -> bool:
        return self._pos >= len(self._source)

    def _advance(self) -> str:
        ch = self._source[self._pos]
        self._pos += 1
        self._col += 1
        return ch

    def _peek(self) -> str:
        if self._at_end():
            return "\0"
        return self._source[self._pos]

    def _peek2(self) -> str:
        if self._pos + 1 >= len(self._source):
            return "\0"
        return self._source[self._pos + 1]

    def _add(self, ttype: TokenType, value: str, line: int, col: int) -> None:
        self._tokens.append(Token(ttype, value, line, col))
