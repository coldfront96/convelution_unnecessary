"""
Recursive-descent parser for STL.

Consumes a Token list (from Lexer) and produces an AST (Program node).
Grammar (informal EBNF):

    program     ::= macro_def* expression EOF
    macro_def   ::= "macro" IDENT "=" expression
    expression  ::= let_expr | conditional | pipeline
    let_expr    ::= "let" IDENT "=" expression (";" expression)?
    conditional ::= "if" comparison "then" expression "else" expression
    comparison  ::= additive (CMP_OP additive)?
    additive    ::= primary ("+" primary)*
    pipeline    ::= transform ("|" transform)*
    transform   ::= call | atom
    call        ::= IDENT "(" arg_list ")"
    atom        ::= IDENT | STRING | NUMBER | "true" | "false"
                  | "(" expression ")"
    arg_list    ::= (positional_arg | kwarg_arg) ("," (positional_arg | kwarg_arg))*
    kwarg_arg   ::= IDENT "=" expression
    positional_arg ::= expression
    lambda      ::= IDENT "=>" expression
"""

from __future__ import annotations

from typing import Dict, List, Optional

from stratum.lang.ast_nodes import (
    AstNode,
    BinaryOp,
    Call,
    Conditional,
    Identifier,
    Lambda,
    LetBinding,
    MacroDef,
    MacroRef,
    NumberLiteral,
    Pipeline,
    Program,
    StringLiteral,
)
from stratum.lang.lexer import Token, TokenType


class ParseError(Exception):
    def __init__(self, message: str, token: Token) -> None:
        super().__init__(f"{message} (got {token.type.name} {token.value!r} at {token.line}:{token.col})")
        self.token = token


_CMP_OPS = {
    TokenType.EQEQ: "==",
    TokenType.NEQ:  "!=",
    TokenType.LT:   "<",
    TokenType.LTE:  "<=",
    TokenType.GT:   ">",
    TokenType.GTE:  ">=",
}


class Parser:
    """
    Recursive-descent parser.

    Maintains a cursor over the token list.
    Each parse_* method corresponds to a grammar rule.
    """

    def __init__(self, tokens: List[Token]) -> None:
        self._tokens = tokens
        self._pos = 0

    def parse(self) -> Program:
        """Entry point. Parses a complete program."""
        macros: List[MacroDef] = []
        while self._check(TokenType.KW_MACRO):
            macros.append(self._parse_macro_def())
            self._match(TokenType.SEMICOL)  # optional separator between macros and body
        body = self._parse_expression() if not self._check(TokenType.EOF) else None
        self._expect(TokenType.EOF)
        return Program(macros=macros, body=body)

    # ---- grammar rules ------------------------------------------------------

    def _parse_macro_def(self) -> MacroDef:
        self._expect(TokenType.KW_MACRO)
        name_tok = self._expect(TokenType.IDENT)
        self._expect(TokenType.EQ)
        body = self._parse_expression()
        return MacroDef(name=name_tok.value, body=body)

    def _parse_expression(self) -> AstNode:
        if self._check(TokenType.KW_LET):
            return self._parse_let()
        if self._check(TokenType.KW_IF):
            return self._parse_conditional()
        return self._parse_pipeline()

    def _parse_let(self) -> LetBinding:
        self._expect(TokenType.KW_LET)
        name_tok = self._expect(TokenType.IDENT)
        self._expect(TokenType.EQ)
        value = self._parse_expression()
        body: Optional[AstNode] = None
        if self._match(TokenType.SEMICOL):
            body = self._parse_expression()
        return LetBinding(name=name_tok.value, value=value, body=body)

    def _parse_conditional(self) -> Conditional:
        self._expect(TokenType.KW_IF)
        condition = self._parse_comparison()
        self._expect(TokenType.KW_THEN)
        then_branch = self._parse_pipeline()
        self._expect(TokenType.KW_ELSE)
        else_branch = self._parse_pipeline()
        return Conditional(condition=condition, then_branch=then_branch, else_branch=else_branch)

    def _parse_comparison(self) -> AstNode:
        left = self._parse_additive()
        if self._current().type in _CMP_OPS:
            op_tok = self._advance()
            right = self._parse_additive()
            return BinaryOp(op=_CMP_OPS[op_tok.type], left=left, right=right)
        return left

    def _parse_additive(self) -> AstNode:
        left = self._parse_primary()
        while self._check(TokenType.PLUS):
            self._advance()
            right = self._parse_primary()
            left = BinaryOp(op="+", left=left, right=right)
        return left

    def _parse_pipeline(self) -> AstNode:
        stages: List[AstNode] = [self._parse_transform()]
        while self._match(TokenType.PIPE):
            stages.append(self._parse_transform())
        if len(stages) == 1:
            return stages[0]
        return Pipeline(stages=stages)

    def _parse_transform(self) -> AstNode:
        # lambda check: IDENT "=>" expression
        if self._check(TokenType.IDENT) and self._peek_type(1) == TokenType.ARROW:
            param_tok = self._advance()
            self._advance()  # consume =>
            body = self._parse_expression()
            return Lambda(param=param_tok.value, body=body)

        # named call: IDENT "(" ... ")"
        if self._check(TokenType.IDENT) and self._peek_type(1) == TokenType.LPAREN:
            return self._parse_call()

        return self._parse_atom()

    def _parse_call(self) -> Call:
        name_tok = self._expect(TokenType.IDENT)
        self._expect(TokenType.LPAREN)
        args: List[AstNode] = []
        kwargs: Dict[str, AstNode] = {}

        if not self._check(TokenType.RPAREN):
            self._parse_arg_list(args, kwargs)

        self._expect(TokenType.RPAREN)
        return Call(name=name_tok.value, args=args, kwargs=kwargs)

    def _parse_arg_list(
        self, args: List[AstNode], kwargs: Dict[str, AstNode]
    ) -> None:
        self._parse_one_arg(args, kwargs)
        while self._match(TokenType.COMMA):
            if self._check(TokenType.RPAREN):
                break  # trailing comma
            self._parse_one_arg(args, kwargs)

    def _parse_one_arg(
        self, args: List[AstNode], kwargs: Dict[str, AstNode]
    ) -> None:
        # kwarg: IDENT "=" expr  (only if next-next is "=")
        if self._check(TokenType.IDENT) and self._peek_type(1) == TokenType.EQ:
            key_tok = self._advance()
            self._advance()  # "="
            value = self._parse_expression()
            kwargs[key_tok.value] = value
        else:
            args.append(self._parse_expression())

    def _parse_primary(self) -> AstNode:
        tok = self._current()

        if tok.type == TokenType.IDENT:
            self._advance()
            return Identifier(name=tok.value)

        if tok.type == TokenType.STRING:
            self._advance()
            return StringLiteral(value=tok.value)

        if tok.type == TokenType.NUMBER:
            self._advance()
            raw = tok.value
            val: int | float = float(raw) if "." in raw else int(raw)
            return NumberLiteral(value=val)

        if tok.type == TokenType.KW_TRUE:
            self._advance()
            return NumberLiteral(value=1)

        if tok.type == TokenType.KW_FALSE:
            self._advance()
            return NumberLiteral(value=0)

        if tok.type == TokenType.LPAREN:
            self._advance()
            expr = self._parse_expression()
            self._expect(TokenType.RPAREN)
            return expr

        raise ParseError("Expected expression", tok)

    def _parse_atom(self) -> AstNode:
        return self._parse_primary()

    # ---- token cursor helpers -----------------------------------------------

    def _current(self) -> Token:
        return self._tokens[self._pos]

    def _advance(self) -> Token:
        tok = self._tokens[self._pos]
        if tok.type != TokenType.EOF:
            self._pos += 1
        return tok

    def _check(self, ttype: TokenType) -> bool:
        return self._current().type == ttype

    def _match(self, ttype: TokenType) -> bool:
        if self._check(ttype):
            self._advance()
            return True
        return False

    def _expect(self, ttype: TokenType) -> Token:
        if not self._check(ttype):
            raise ParseError(f"Expected {ttype.name}", self._current())
        return self._advance()

    def _peek_type(self, offset: int) -> TokenType:
        idx = self._pos + offset
        if idx >= len(self._tokens):
            return TokenType.EOF
        return self._tokens[idx].type
