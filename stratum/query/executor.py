"""
HQL executor. Evaluates a QueryAst against a HistoryProjection.

The executor walks the condition tree and filters records,
applies ordering, and applies limit.

Pattern: Interpreter (each AST node type is interpreted directly),
         Iterator (result is lazily filtered then sliced).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, List, Optional, Union

from stratum.projections.history import TransformationRecord
from stratum.query.lexer import QueryLexer, QueryLexError
from stratum.query.parser import (
    AndCondition,
    CompareCondition,
    Condition,
    ContainsCondition,
    EndsWithCondition,
    NotCondition,
    OrCondition,
    QueryAst,
    QueryParseError,
    QueryParser,
    StartsWithCondition,
)

logger = logging.getLogger(__name__)


# ---- result -----------------------------------------------------------------


@dataclass
class QueryResult:
    rows: List[dict] = field(default_factory=list)
    total_matched: int = 0
    fields: List[str] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.rows)

    def to_table(self) -> str:
        """Render as a simple text table."""
        if not self.rows:
            return f"(no results)  total_matched={self.total_matched}"

        cols = self.fields or list(self.rows[0].keys())
        widths = {c: max(len(c), max((len(str(r.get(c, ""))) for r in self.rows), default=0))
                  for c in cols}

        sep = "+" + "+".join("-" * (widths[c] + 2) for c in cols) + "+"
        header = "|" + "|".join(f" {c:<{widths[c]}} " for c in cols) + "|"
        lines = [sep, header, sep]
        for row in self.rows:
            lines.append("|" + "|".join(f" {str(row.get(c, '')):<{widths[c]}} " for c in cols) + "|")
        lines.append(sep)
        lines.append(f"{len(self.rows)} row(s) shown, {self.total_matched} matched")
        return "\n".join(lines)


# ---- record → dict projection -----------------------------------------------


_ALL_FIELDS = ("session_id", "input", "output", "program", "succeeded", "duration_ms", "error")


def _record_to_dict(record: TransformationRecord) -> dict[str, Any]:
    return {
        "session_id":  record.session_id,
        "input":       record.input_text,
        "output":      record.output_text or "",
        "program":     record.program_source,
        "succeeded":   record.succeeded,
        "duration_ms": record.duration_ms,
        "error":       record.error_message or "",
    }


# ---- condition evaluator (Interpreter pattern) ------------------------------


def _eval_condition(cond: Condition, row: dict[str, Any]) -> bool:
    if isinstance(cond, CompareCondition):
        raw = row.get(cond.field)
        if raw is None:
            return False
        # Numeric coercion when comparing with int/float
        lv: Any = raw
        rv: Any = cond.value
        if isinstance(rv, (int, float)) and isinstance(lv, str):
            try:
                lv = type(rv)(lv)
            except (ValueError, TypeError):
                pass
        try:
            return {
                "==": lv == rv,
                "!=": lv != rv,
                "<":  lv <  rv,
                "<=": lv <= rv,
                ">":  lv >  rv,
                ">=": lv >= rv,
            }[cond.op]
        except (TypeError, KeyError):
            return False

    if isinstance(cond, ContainsCondition):
        val = str(row.get(cond.field, ""))
        result = cond.substring in val
        return (not result) if cond.negated else result

    if isinstance(cond, StartsWithCondition):
        val = str(row.get(cond.field, ""))
        return val.startswith(cond.prefix)

    if isinstance(cond, EndsWithCondition):
        val = str(row.get(cond.field, ""))
        return val.endswith(cond.suffix)

    if isinstance(cond, AndCondition):
        return _eval_condition(cond.left, row) and _eval_condition(cond.right, row)

    if isinstance(cond, OrCondition):
        return _eval_condition(cond.left, row) or _eval_condition(cond.right, row)

    if isinstance(cond, NotCondition):
        return not _eval_condition(cond.operand, row)

    return True


# ---- executor ---------------------------------------------------------------


class QueryError(Exception):
    pass


class QueryExecutor:
    """
    Evaluates HQL queries against a HistoryProjection.

    Usage:
        executor = QueryExecutor(history_projection)
        result = executor.execute("SELECT * WHERE input CONTAINS 'hello' LIMIT 5")
    """

    def __init__(self, history_projection: Any) -> None:
        self._history = history_projection

    def execute(self, query_source: str) -> QueryResult:
        try:
            tokens = QueryLexer(query_source).tokenize()
            ast = QueryParser(tokens).parse()
        except (QueryLexError, QueryParseError) as exc:
            raise QueryError(f"Parse error: {exc}") from exc

        return self._execute_ast(ast)

    def _execute_ast(self, ast: QueryAst) -> QueryResult:
        records = self._history.all()
        rows = [_record_to_dict(r) for r in records]

        # Filter
        if ast.condition:
            rows = [r for r in rows if _eval_condition(ast.condition, r)]

        total_matched = len(rows)

        # Order
        if ast.order:
            key = ast.order.field
            rows.sort(
                key=lambda r: (r.get(key) is None, r.get(key, "")),
                reverse=ast.order.descending,
            )

        # Limit
        if ast.limit is not None:
            rows = rows[: ast.limit]

        # Project fields
        if ast.fields.is_star:
            projected_fields = list(_ALL_FIELDS)
        else:
            projected_fields = ast.fields.fields
            unknown = [f for f in projected_fields if f not in _ALL_FIELDS]
            if unknown:
                logger.warning("HQL: unknown fields in SELECT: %s", unknown)

        projected_rows = [
            {f: r.get(f, "") for f in projected_fields}
            for r in rows
        ]

        return QueryResult(
            rows=projected_rows,
            total_matched=total_matched,
            fields=projected_fields,
        )
