"""
Parser for HQL (History Query Language).

Produces a QueryAst from a token list.

Grammar:
    query       ::= select_clause where_clause? order_clause? limit_clause? EOF
    select_clause ::= SELECT (STAR | field_list)
    field_list  ::= IDENT (COMMA IDENT)*
    where_clause ::= WHERE condition
    condition   ::= and_expr (OR and_expr)*
    and_expr    ::= not_expr (AND not_expr)*
    not_expr    ::= NOT not_expr | primary_cond
    primary_cond ::= IDENT op value
                   | IDENT CONTAINS STRING
                   | IDENT STARTS WITH STRING
                   | IDENT ENDS WITH STRING
                   | "(" condition ")"
    op          ::= EQ | NEQ | LT | LTE | GT | GTE
    value       ::= STRING | NUMBER | TRUE | FALSE | NULL
    order_clause ::= ORDER BY IDENT (ASC | DESC)?
    limit_clause ::= LIMIT NUMBER

Pattern: Recursive descent (same approach as the STL parser, second DSL).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Union

from stratum.query.lexer import QueryToken, QueryTokenType


# ---- AST nodes --------------------------------------------------------------


@dataclass
class FieldList:
    fields: List[str]  # empty list means SELECT *

    @property
    def is_star(self) -> bool:
        return not self.fields


@dataclass
class CompareCondition:
    field: str
    op: str
    value: Union[str, int, float, bool, None]


@dataclass
class ContainsCondition:
    field: str
    substring: str
    negated: bool = False


@dataclass
class StartsWithCondition:
    field: str
    prefix: str


@dataclass
class EndsWithCondition:
    field: str
    suffix: str


@dataclass
class AndCondition:
    left: "Condition"
    right: "Condition"


@dataclass
class OrCondition:
    left: "Condition"
    right: "Condition"


@dataclass
class NotCondition:
    operand: "Condition"


Condition = Union[
    CompareCondition,
    ContainsCondition,
    StartsWithCondition,
    EndsWithCondition,
    AndCondition,
    OrCondition,
    NotCondition,
]


@dataclass
class OrderClause:
    field: str
    descending: bool = False


@dataclass
class QueryAst:
    """Root node of a parsed HQL query."""
    fields: FieldList = field(default_factory=lambda: FieldList([]))
    condition: Optional[Condition] = None
    order: Optional[OrderClause] = None
    limit: Optional[int] = None


# ---- parser -----------------------------------------------------------------


class QueryParseError(Exception):
    def __init__(self, msg: str, token: QueryToken):
        super().__init__(f"{msg} (at pos {token.pos}, got {token.type.name} {token.value!r})")
        self.token = token


class QueryParser:
    """Recursive-descent parser for HQL."""

    def __init__(self, tokens: List[QueryToken]) -> None:
        self._tokens = tokens
        self._pos = 0

    def parse(self) -> QueryAst:
        ast = QueryAst()
        ast.fields = self._parse_select()
        if self._check(QueryTokenType.KW_WHERE):
            self._advance()
            ast.condition = self._parse_condition()
        if self._check(QueryTokenType.KW_ORDER):
            ast.order = self._parse_order()
        if self._check(QueryTokenType.KW_LIMIT):
            ast.limit = self._parse_limit()
        self._expect(QueryTokenType.EOF)
        return ast

    def _parse_select(self) -> FieldList:
        self._expect(QueryTokenType.KW_SELECT)
        if self._match(QueryTokenType.STAR):
            return FieldList([])
        fields: List[str] = []
        fields.append(self._expect(QueryTokenType.IDENT).value)
        while self._match(QueryTokenType.COMMA):
            fields.append(self._expect(QueryTokenType.IDENT).value)
        return FieldList(fields)

    def _parse_condition(self) -> Condition:
        return self._parse_or_expr()

    def _parse_or_expr(self) -> Condition:
        left = self._parse_and_expr()
        while self._check(QueryTokenType.KW_OR):
            self._advance()
            right = self._parse_and_expr()
            left = OrCondition(left=left, right=right)
        return left

    def _parse_and_expr(self) -> Condition:
        left = self._parse_not_expr()
        while self._check(QueryTokenType.KW_AND):
            self._advance()
            right = self._parse_not_expr()
            left = AndCondition(left=left, right=right)
        return left

    def _parse_not_expr(self) -> Condition:
        if self._match(QueryTokenType.KW_NOT):
            return NotCondition(operand=self._parse_not_expr())
        return self._parse_primary_cond()

    def _parse_primary_cond(self) -> Condition:
        field_tok = self._expect(QueryTokenType.IDENT)
        field_name = field_tok.value

        if self._check(QueryTokenType.KW_CONTAINS):
            self._advance()
            value_tok = self._expect(QueryTokenType.STRING)
            return ContainsCondition(field=field_name, substring=value_tok.value)

        if self._check(QueryTokenType.KW_STARTS):
            self._advance()
            self._expect(QueryTokenType.KW_WITH)
            value_tok = self._expect(QueryTokenType.STRING)
            return StartsWithCondition(field=field_name, prefix=value_tok.value)

        if self._check(QueryTokenType.KW_ENDS):
            self._advance()
            self._expect(QueryTokenType.KW_WITH)
            value_tok = self._expect(QueryTokenType.STRING)
            return EndsWithCondition(field=field_name, suffix=value_tok.value)

        # Comparison operator
        op_tok = self._current()
        op_map = {
            QueryTokenType.EQ:  "==",
            QueryTokenType.NEQ: "!=",
            QueryTokenType.LT:  "<",
            QueryTokenType.LTE: "<=",
            QueryTokenType.GT:  ">",
            QueryTokenType.GTE: ">=",
        }
        if op_tok.type not in op_map:
            raise QueryParseError(
                f"Expected comparison operator after field '{field_name}'", op_tok
            )
        self._advance()
        op = op_map[op_tok.type]
        value = self._parse_value()
        return CompareCondition(field=field_name, op=op, value=value)

    def _parse_value(self) -> Union[str, int, float, bool, None]:
        tok = self._current()
        if tok.type == QueryTokenType.STRING:
            self._advance()
            return tok.value
        if tok.type == QueryTokenType.NUMBER:
            self._advance()
            raw = tok.value
            return float(raw) if "." in raw else int(raw)
        if tok.type == QueryTokenType.KW_TRUE:
            self._advance()
            return True
        if tok.type == QueryTokenType.KW_FALSE:
            self._advance()
            return False
        if tok.type == QueryTokenType.KW_NULL:
            self._advance()
            return None
        raise QueryParseError("Expected a literal value (string, number, true, false, null)", tok)

    def _parse_order(self) -> OrderClause:
        self._expect(QueryTokenType.KW_ORDER)
        self._expect(QueryTokenType.KW_BY)
        field_tok = self._expect(QueryTokenType.IDENT)
        descending = False
        if self._match(QueryTokenType.KW_DESC):
            descending = True
        elif self._match(QueryTokenType.KW_ASC):
            descending = False
        return OrderClause(field=field_tok.value, descending=descending)

    def _parse_limit(self) -> int:
        self._expect(QueryTokenType.KW_LIMIT)
        tok = self._expect(QueryTokenType.NUMBER)
        return int(tok.value)

    # ---- cursor helpers -----------------------------------------------------

    def _current(self) -> QueryToken:
        return self._tokens[self._pos]

    def _advance(self) -> QueryToken:
        tok = self._tokens[self._pos]
        if tok.type != QueryTokenType.EOF:
            self._pos += 1
        return tok

    def _check(self, ttype: QueryTokenType) -> bool:
        return self._current().type == ttype

    def _match(self, ttype: QueryTokenType) -> bool:
        if self._check(ttype):
            self._advance()
            return True
        return False

    def _expect(self, ttype: QueryTokenType) -> QueryToken:
        if not self._check(ttype):
            raise QueryParseError(f"Expected {ttype.name}", self._current())
        return self._advance()
