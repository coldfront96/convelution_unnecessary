"""Tests for the History Query Language (HQL) — lexer, parser, executor."""

import pytest
from unittest.mock import MagicMock

from stratum.query.lexer import QueryLexer, QueryTokenType, QueryLexError  # noqa: F401 used in test
from stratum.query.parser import (
    QueryParser,
    QueryAst,
    CompareCondition,
    ContainsCondition,
    StartsWithCondition,
    EndsWithCondition,
    AndCondition,
    OrCondition,
    NotCondition,
    QueryParseError,
)
from stratum.query.executor import QueryExecutor, QueryResult, QueryError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def lex(source: str):
    return QueryLexer(source).tokenize()


def parse(source: str) -> QueryAst:
    return QueryParser(lex(source)).parse()


def _make_record(
    session_id="s1",
    input_text="hello",
    output_text="HELLO",
    program_source="upper",
    succeeded=True,
    duration_ms=10.0,
    error_message=None,
):
    r = MagicMock()
    r.session_id = session_id
    r.input_text = input_text
    r.output_text = output_text
    r.program_source = program_source
    r.succeeded = succeeded
    r.duration_ms = duration_ms
    r.error_message = error_message
    return r


def _make_projection(*records):
    proj = MagicMock()
    proj.all.return_value = list(records)
    return proj


# ---------------------------------------------------------------------------
# Lexer tests
# ---------------------------------------------------------------------------


def test_lex_select_star():
    tokens = lex("SELECT *")
    types = [t.type for t in tokens]
    assert QueryTokenType.KW_SELECT in types
    assert QueryTokenType.STAR in types


def test_lex_keywords():
    tokens = lex("SELECT WHERE ORDER BY LIMIT AND OR NOT")
    types = {t.type for t in tokens}
    assert QueryTokenType.KW_SELECT in types
    assert QueryTokenType.KW_WHERE in types
    assert QueryTokenType.KW_ORDER in types
    assert QueryTokenType.KW_BY in types
    assert QueryTokenType.KW_LIMIT in types
    assert QueryTokenType.KW_AND in types
    assert QueryTokenType.KW_OR in types
    assert QueryTokenType.KW_NOT in types


def test_lex_contains_starts_ends_with():
    tokens = lex("input CONTAINS 'hi' input STARTS WITH 'h' input ENDS WITH 'o'")
    types = [t.type for t in tokens]
    assert QueryTokenType.KW_CONTAINS in types
    assert QueryTokenType.KW_STARTS in types
    assert QueryTokenType.KW_ENDS in types
    assert QueryTokenType.KW_WITH in types


def test_lex_operators():
    tokens = lex("= != < <= > >=")
    types = [t.type for t in tokens if t.type != QueryTokenType.EOF]
    assert types == [
        QueryTokenType.EQ,
        QueryTokenType.NEQ,
        QueryTokenType.LT,
        QueryTokenType.LTE,
        QueryTokenType.GT,
        QueryTokenType.GTE,
    ]


def test_lex_string_double_quotes():
    tokens = lex('"hello world"')
    strings = [t for t in tokens if t.type == QueryTokenType.STRING]
    assert strings[0].value == "hello world"


def test_lex_string_single_quotes():
    tokens = lex("'test'")
    strings = [t for t in tokens if t.type == QueryTokenType.STRING]
    assert strings[0].value == "test"


def test_lex_number_integer():
    tokens = lex("42")
    numbers = [t for t in tokens if t.type == QueryTokenType.NUMBER]
    assert numbers[0].value == "42"


def test_lex_number_float():
    tokens = lex("3.14")
    numbers = [t for t in tokens if t.type == QueryTokenType.NUMBER]
    assert numbers[0].value == "3.14"


def test_lex_negative_number():
    tokens = lex("-5")
    numbers = [t for t in tokens if t.type == QueryTokenType.NUMBER]
    assert numbers[0].value == "-5"


def test_lex_true_false_null():
    tokens = lex("true false null")
    types = [t.type for t in tokens if t.type != QueryTokenType.EOF]
    assert QueryTokenType.KW_TRUE in types
    assert QueryTokenType.KW_FALSE in types
    assert QueryTokenType.KW_NULL in types


def test_lex_comma_ident():
    tokens = lex("input, output")
    types = [t.type for t in tokens if t.type != QueryTokenType.EOF]
    assert QueryTokenType.IDENT in types
    assert QueryTokenType.COMMA in types


def test_lex_unknown_char_raises():
    with pytest.raises(QueryLexError):
        lex("SELECT @")


def test_lex_unterminated_string_raises():
    with pytest.raises(QueryLexError):
        lex('"unclosed')


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


def test_parse_select_star():
    ast = parse("SELECT *")
    assert ast.fields.is_star


def test_parse_select_fields():
    ast = parse("SELECT input, output, duration_ms")
    assert not ast.fields.is_star
    assert ast.fields.fields == ["input", "output", "duration_ms"]


def test_parse_where_compare_eq():
    ast = parse("SELECT * WHERE succeeded = true")
    assert isinstance(ast.condition, CompareCondition)
    assert ast.condition.field == "succeeded"
    assert ast.condition.op == "=="
    assert ast.condition.value is True


def test_parse_where_compare_number():
    ast = parse("SELECT * WHERE duration_ms > 50")
    assert isinstance(ast.condition, CompareCondition)
    assert ast.condition.field == "duration_ms"
    assert ast.condition.op == ">"
    assert ast.condition.value == 50


def test_parse_where_contains():
    ast = parse("SELECT * WHERE input CONTAINS 'hello'")
    assert isinstance(ast.condition, ContainsCondition)
    assert ast.condition.field == "input"
    assert ast.condition.substring == "hello"
    assert not ast.condition.negated


def test_parse_where_starts_with():
    ast = parse("SELECT * WHERE output STARTS WITH 'HI'")
    assert isinstance(ast.condition, StartsWithCondition)
    assert ast.condition.field == "output"
    assert ast.condition.prefix == "HI"


def test_parse_where_ends_with():
    ast = parse("SELECT * WHERE program ENDS WITH 'upper'")
    assert isinstance(ast.condition, EndsWithCondition)
    assert ast.condition.field == "program"
    assert ast.condition.suffix == "upper"


def test_parse_and_condition():
    ast = parse("SELECT * WHERE succeeded = true AND duration_ms < 100")
    assert isinstance(ast.condition, AndCondition)
    assert isinstance(ast.condition.left, CompareCondition)
    assert isinstance(ast.condition.right, CompareCondition)


def test_parse_or_condition():
    ast = parse("SELECT * WHERE succeeded = true OR duration_ms > 100")
    assert isinstance(ast.condition, OrCondition)


def test_parse_not_condition():
    ast = parse("SELECT * WHERE NOT succeeded = true")
    assert isinstance(ast.condition, NotCondition)
    assert isinstance(ast.condition.operand, CompareCondition)


def test_parse_order_by_asc():
    ast = parse("SELECT * ORDER BY duration_ms ASC")
    assert ast.order is not None
    assert ast.order.field == "duration_ms"
    assert not ast.order.descending


def test_parse_order_by_desc():
    ast = parse("SELECT * ORDER BY duration_ms DESC")
    assert ast.order.descending


def test_parse_limit():
    ast = parse("SELECT * LIMIT 5")
    assert ast.limit == 5


def test_parse_full_query():
    q = "SELECT input, output WHERE input CONTAINS 'hi' ORDER BY duration_ms DESC LIMIT 3"
    ast = parse(q)
    assert not ast.fields.is_star
    assert ast.fields.fields == ["input", "output"]
    assert isinstance(ast.condition, ContainsCondition)
    assert ast.order.descending
    assert ast.limit == 3


def test_parse_error_missing_select():
    with pytest.raises(QueryParseError):
        parse("WHERE input = 'x'")


def test_parse_error_bad_value():
    with pytest.raises((QueryParseError, QueryLexError)):
        parse("SELECT * WHERE input = !")


# ---------------------------------------------------------------------------
# Executor tests
# ---------------------------------------------------------------------------


def test_executor_select_star_no_filter():
    records = [_make_record("s1", "hello", "HELLO"), _make_record("s2", "world", "WORLD")]
    proj = _make_projection(*records)
    exe = QueryExecutor(proj)
    result = exe.execute("SELECT *")
    assert result.total_matched == 2
    assert len(result.rows) == 2
    assert result.fields == list(("session_id", "input", "output", "program", "succeeded", "duration_ms", "error"))


def test_executor_filter_by_succeeded():
    records = [
        _make_record("s1", succeeded=True),
        _make_record("s2", succeeded=False),
    ]
    exe = QueryExecutor(_make_projection(*records))
    result = exe.execute("SELECT * WHERE succeeded = true")
    assert result.total_matched == 1
    assert result.rows[0]["session_id"] == "s1"


def test_executor_filter_contains():
    records = [
        _make_record("s1", input_text="hello world"),
        _make_record("s2", input_text="goodbye"),
    ]
    exe = QueryExecutor(_make_projection(*records))
    result = exe.execute("SELECT * WHERE input CONTAINS 'hello'")
    assert result.total_matched == 1
    assert result.rows[0]["session_id"] == "s1"


def test_executor_filter_starts_with():
    records = [
        _make_record("s1", input_text="prefix_data"),
        _make_record("s2", input_text="other_data"),
    ]
    exe = QueryExecutor(_make_projection(*records))
    result = exe.execute("SELECT * WHERE input STARTS WITH 'prefix'")
    assert result.total_matched == 1


def test_executor_filter_ends_with():
    records = [
        _make_record("s1", output_text="result_ok"),
        _make_record("s2", output_text="result_fail"),
    ]
    exe = QueryExecutor(_make_projection(*records))
    result = exe.execute("SELECT * WHERE output ENDS WITH '_ok'")
    assert result.total_matched == 1


def test_executor_filter_duration_gt():
    records = [
        _make_record("s1", duration_ms=10.0),
        _make_record("s2", duration_ms=100.0),
        _make_record("s3", duration_ms=200.0),
    ]
    exe = QueryExecutor(_make_projection(*records))
    result = exe.execute("SELECT * WHERE duration_ms > 50")
    assert result.total_matched == 2


def test_executor_order_by_duration_asc():
    records = [
        _make_record("s1", duration_ms=30.0),
        _make_record("s2", duration_ms=10.0),
        _make_record("s3", duration_ms=20.0),
    ]
    exe = QueryExecutor(_make_projection(*records))
    result = exe.execute("SELECT * ORDER BY duration_ms ASC")
    durations = [r["duration_ms"] for r in result.rows]
    assert durations == sorted(durations)


def test_executor_order_by_duration_desc():
    records = [
        _make_record("s1", duration_ms=30.0),
        _make_record("s2", duration_ms=10.0),
        _make_record("s3", duration_ms=20.0),
    ]
    exe = QueryExecutor(_make_projection(*records))
    result = exe.execute("SELECT * ORDER BY duration_ms DESC")
    durations = [r["duration_ms"] for r in result.rows]
    assert durations == sorted(durations, reverse=True)


def test_executor_limit():
    records = [_make_record(f"s{i}") for i in range(10)]
    exe = QueryExecutor(_make_projection(*records))
    result = exe.execute("SELECT * LIMIT 3")
    assert len(result.rows) == 3
    assert result.total_matched == 10


def test_executor_select_specific_fields():
    records = [_make_record("s1", input_text="hi", output_text="HI")]
    exe = QueryExecutor(_make_projection(*records))
    result = exe.execute("SELECT input, output")
    assert result.fields == ["input", "output"]
    assert set(result.rows[0].keys()) == {"input", "output"}


def test_executor_and_condition():
    records = [
        _make_record("s1", succeeded=True, duration_ms=5.0),
        _make_record("s2", succeeded=True, duration_ms=200.0),
        _make_record("s3", succeeded=False, duration_ms=5.0),
    ]
    exe = QueryExecutor(_make_projection(*records))
    result = exe.execute("SELECT * WHERE succeeded = true AND duration_ms < 100")
    assert result.total_matched == 1
    assert result.rows[0]["session_id"] == "s1"


def test_executor_or_condition():
    records = [
        _make_record("s1", duration_ms=5.0),
        _make_record("s2", duration_ms=150.0),
        _make_record("s3", duration_ms=500.0),
    ]
    exe = QueryExecutor(_make_projection(*records))
    result = exe.execute("SELECT * WHERE duration_ms < 10 OR duration_ms > 200")
    assert result.total_matched == 2


def test_executor_not_condition():
    records = [
        _make_record("s1", succeeded=True),
        _make_record("s2", succeeded=False),
    ]
    exe = QueryExecutor(_make_projection(*records))
    result = exe.execute("SELECT * WHERE NOT succeeded = true")
    assert result.total_matched == 1
    assert result.rows[0]["session_id"] == "s2"


def test_executor_empty_history():
    exe = QueryExecutor(_make_projection())
    result = exe.execute("SELECT *")
    assert result.total_matched == 0
    assert len(result.rows) == 0


def test_executor_parse_error_raises_query_error():
    exe = QueryExecutor(_make_projection())
    with pytest.raises(QueryError):
        exe.execute("INVALID QUERY SYNTAX !!!")


def test_executor_no_results_table():
    exe = QueryExecutor(_make_projection())
    result = exe.execute("SELECT *")
    table = result.to_table()
    assert "no results" in table.lower() or "total_matched" in table


def test_executor_to_table_has_headers():
    records = [_make_record("s1")]
    exe = QueryExecutor(_make_projection(*records))
    result = exe.execute("SELECT input, output")
    table = result.to_table()
    assert "input" in table
    assert "output" in table


def test_executor_full_query_integration():
    records = [
        _make_record("s1", input_text="hello", output_text="HELLO", duration_ms=10.0, succeeded=True),
        _make_record("s2", input_text="hi there", output_text="HI THERE", duration_ms=50.0, succeeded=True),
        _make_record("s3", input_text="bye", output_text="", duration_ms=5.0, succeeded=False),
    ]
    exe = QueryExecutor(_make_projection(*records))
    result = exe.execute(
        "SELECT input, output WHERE succeeded = true AND input CONTAINS 'h' ORDER BY duration_ms DESC LIMIT 2"
    )
    assert result.total_matched == 2
    assert len(result.rows) == 2
    assert result.rows[0]["input"] == "hi there"  # higher duration first
