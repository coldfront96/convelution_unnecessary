"""Tests for the REPL and its session state."""

import json
import pytest
from pathlib import Path
from unittest.mock import MagicMock

from stratum.repl.session import ReplSession, MacroEntry, SessionSnapshot
from stratum.repl.repl import Repl
from stratum.core.result import Ok, Err


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_orch(transform_fn=None):
    """Build a mock orchestrator."""
    orch = MagicMock()
    if transform_fn is None:
        orch.transform.side_effect = lambda inp, prog: Ok(inp.upper())
    else:
        orch.transform.side_effect = transform_fn
    orch.typecheck.return_value = Ok("String — OK")
    orch.disassemble.return_value = Ok("; type: String\nHALT")
    return orch


def _make_repl(orch=None, session_path=None):
    if orch is None:
        orch = _make_orch()
    hist = MagicMock()
    import io
    out = io.StringIO()
    return Repl(
        orchestrator=orch,
        history_projection=hist,
        session_path=session_path,
        output=out,
    ), out


# ---------------------------------------------------------------------------
# ReplSession unit tests
# ---------------------------------------------------------------------------


def test_session_define_macro():
    s = ReplSession()
    s.define_macro("shout", "upper | append('!')")
    assert len(s.macros) == 1
    assert s.macros[0].name == "shout"
    assert s.macros[0].source == "upper | append('!')"


def test_session_define_macro_replaces_existing():
    s = ReplSession()
    s.define_macro("shout", "upper")
    s.define_macro("shout", "lower")
    assert len(s.macros) == 1
    assert s.macros[0].source == "lower"


def test_session_remove_macro():
    s = ReplSession()
    s.define_macro("foo", "upper")
    assert s.remove_macro("foo") is True
    assert len(s.macros) == 0


def test_session_remove_nonexistent_macro():
    s = ReplSession()
    assert s.remove_macro("nope") is False


def test_session_get_macro():
    s = ReplSession()
    s.define_macro("foo", "upper")
    m = s.get_macro("foo")
    assert m is not None
    assert m.name == "foo"


def test_session_macro_names():
    s = ReplSession()
    s.define_macro("a", "upper")
    s.define_macro("b", "lower")
    assert s.macro_names() == ["a", "b"]


def test_session_build_preamble_empty():
    s = ReplSession()
    assert s.build_preamble() == ""


def test_session_build_preamble_with_macros():
    s = ReplSession()
    s.define_macro("shout", "upper | append('!')")
    preamble = s.build_preamble()
    assert "macro shout" in preamble
    assert "upper | append('!')" in preamble
    assert preamble.endswith("; ")


def test_session_wrap_program():
    s = ReplSession()
    s.define_macro("shout", "upper")
    wrapped = s.wrap_program("shout")
    assert wrapped == "macro shout = upper; shout"


def test_session_wrap_program_no_macros():
    s = ReplSession()
    assert s.wrap_program("upper") == "upper"


def test_session_push_history():
    s = ReplSession()
    s.push_history("upper")
    s.push_history("lower")
    assert s.recent_history(10) == ["upper", "lower"]


def test_session_push_history_empty_ignored():
    s = ReplSession()
    s.push_history("   ")
    assert s.recent_history() == []


def test_session_recent_history_limit():
    s = ReplSession()
    for i in range(30):
        s.push_history(str(i))
    assert len(s.recent_history(5)) == 5
    assert s.recent_history(5) == ["25", "26", "27", "28", "29"]


def test_session_clear_macros():
    s = ReplSession()
    s.define_macro("a", "upper")
    s.clear_macros()
    assert s.macros == []


def test_session_reset():
    s = ReplSession()
    s.current_input = "hello"
    s.define_macro("a", "upper")
    s.push_history("upper")
    s.reset()
    assert s.current_input == ""
    assert s.macros == []
    assert s.recent_history() == []


def test_session_snapshot_and_restore():
    s = ReplSession(current_input="hello")
    s.define_macro("a", "upper")
    snap = s.snapshot()

    s.current_input = "world"
    s.define_macro("b", "lower")
    s.restore(snap)

    assert s.current_input == "hello"
    assert s.macro_names() == ["a"]


def test_session_snapshot_is_independent():
    s = ReplSession(current_input="hello")
    snap = s.snapshot()
    s.current_input = "changed"
    # snap should not reflect the mutation
    assert snap.current_input == "hello"


def test_session_save_and_load(tmp_path):
    path = tmp_path / "session.json"
    s = ReplSession(current_input="test_input")
    s.define_macro("shout", "upper | append('!')")
    s.save(path)

    loaded = ReplSession.load(path)
    assert loaded.current_input == "test_input"
    assert len(loaded.macros) == 1
    assert loaded.macros[0].name == "shout"


def test_session_load_missing_file(tmp_path):
    s = ReplSession.load(tmp_path / "nonexistent.json")
    assert s.current_input == ""
    assert s.macros == []


def test_session_load_corrupt_file(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{{{not json")
    s = ReplSession.load(path)
    assert s.current_input == ""  # falls back to defaults


def test_session_save_json_structure(tmp_path):
    path = tmp_path / "session.json"
    s = ReplSession(current_input="hi")
    s.define_macro("m", "upper")
    s.save(path)
    data = json.loads(path.read_text())
    assert data["current_input"] == "hi"
    assert data["macros"][0] == {"name": "m", "source": "upper"}


# ---------------------------------------------------------------------------
# Repl unit tests (non-interactive via execute_line)
# ---------------------------------------------------------------------------


def test_repl_transform_basic():
    orch = _make_orch(lambda inp, prog: Ok(inp.upper()))
    repl, _ = _make_repl(orch)
    repl._session.current_input = "hello"
    result = repl.execute_line("upper")
    assert result == "HELLO"
    assert repl._session.current_input == "HELLO"


def test_repl_transform_error_does_not_change_state():
    orch = _make_orch(lambda inp, prog: Err("bad plugin"))
    repl, _ = _make_repl(orch)
    repl._session.current_input = "hello"
    result = repl.execute_line("nonexistent_plugin")
    assert "Error" in result
    assert repl._session.current_input == "hello"  # unchanged


def test_repl_dot_set():
    repl, _ = _make_repl()
    result = repl.execute_line(".set hello world")
    assert "hello world" in result
    assert repl._session.current_input == "hello world"


def test_repl_dot_get():
    repl, _ = _make_repl()
    repl._session.current_input = "current"
    result = repl.execute_line(".get")
    assert "current" in result


def test_repl_dot_macro_define():
    # Build an orchestrator that succeeds on probe then on actual use
    orch = _make_orch(lambda inp, prog: Ok(inp + "_ok"))
    repl, _ = _make_repl(orch)
    result = repl.execute_line(".macro shout = upper")
    assert "shout" in result
    assert "defined" in result.lower()
    assert "shout" in repl._session.macro_names()


def test_repl_dot_macros_empty():
    repl, _ = _make_repl()
    result = repl.execute_line(".macros")
    assert "no macros" in result.lower()


def test_repl_dot_macros_lists_macros(tmp_path):
    repl, _ = _make_repl(_make_orch(lambda inp, prog: Ok(inp)))
    repl._session.define_macro("foo", "upper")
    result = repl.execute_line(".macros")
    assert "foo" in result


def test_repl_dot_delmacro():
    repl, _ = _make_repl()
    repl._session.define_macro("foo", "upper")
    result = repl.execute_line(".delmacro foo")
    assert "removed" in result.lower()
    assert "foo" not in repl._session.macro_names()


def test_repl_dot_delmacro_nonexistent():
    repl, _ = _make_repl()
    result = repl.execute_line(".delmacro nope")
    assert "no macro" in result.lower()


def test_repl_dot_clear():
    repl, _ = _make_repl()
    repl._session.current_input = "hello"
    repl._session.define_macro("x", "upper")
    result = repl.execute_line(".clear")
    assert "cleared" in result.lower()
    assert repl._session.current_input == ""
    assert repl._session.macros == []


def test_repl_dot_undo():
    call_count = [0]
    def transform(inp, prog):
        call_count[0] += 1
        return Ok(inp + "_transformed")
    orch = _make_orch(transform)
    repl, _ = _make_repl(orch)
    repl._session.current_input = "original"
    repl.execute_line("upper")
    assert repl._session.current_input == "original_transformed"
    result = repl.execute_line(".undo")
    assert "original" in result
    assert repl._session.current_input == "original"


def test_repl_dot_undo_nothing():
    repl, _ = _make_repl()
    result = repl.execute_line(".undo")
    assert "nothing" in result.lower()


def test_repl_dot_history():
    repl, _ = _make_repl()
    repl._session.push_history("upper")
    repl._session.push_history("lower")
    result = repl.execute_line(".history")
    assert "upper" in result
    assert "lower" in result


def test_repl_dot_history_empty():
    repl, _ = _make_repl()
    result = repl.execute_line(".history")
    assert "no history" in result.lower()


def test_repl_dot_save(tmp_path):
    path = tmp_path / "session.json"
    repl, _ = _make_repl(session_path=path)
    repl._session.current_input = "persisted"
    result = repl.execute_line(".save")
    assert "saved" in result.lower()
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["current_input"] == "persisted"


def test_repl_dot_typecheck():
    orch = _make_orch()
    orch.typecheck.return_value = Ok("String — OK  (no diagnostics)")
    repl, _ = _make_repl(orch)
    result = repl.execute_line(".typecheck upper")
    assert "String" in result


def test_repl_dot_disasm():
    orch = _make_orch()
    orch.disassemble.return_value = Ok("; type: String\n0000 CALL_PLUGIN upper")
    repl, _ = _make_repl(orch)
    result = repl.execute_line(".disasm upper")
    assert "CALL_PLUGIN" in result


def test_repl_dot_help():
    repl, _ = _make_repl()
    result = repl.execute_line(".help")
    assert ".quit" in result
    assert ".macro" in result


def test_repl_dot_quit():
    repl, _ = _make_repl()
    result = repl.execute_line(".quit")
    assert result == "__quit__"


def test_repl_dot_exit():
    repl, _ = _make_repl()
    result = repl.execute_line(".exit")
    assert result == "__quit__"


def test_repl_unknown_dot_command():
    repl, _ = _make_repl()
    result = repl.execute_line(".flibbertigibbet")
    assert "unknown" in result.lower()


def test_repl_empty_line_returns_none():
    repl, _ = _make_repl()
    result = repl.execute_line("")
    assert result is None


def test_repl_preamble_injected_on_execution():
    """Macros in session are prepended to each expression."""
    captured = {}
    def transform(inp, prog):
        captured["prog"] = prog
        return Ok("ok")
    orch = _make_orch(transform)
    repl, _ = _make_repl(orch)
    repl._session.define_macro("shout", "upper")
    repl.execute_line("shout")
    assert "macro shout = upper" in captured["prog"]


def test_repl_session_persisted_across_instances(tmp_path):
    path = tmp_path / "persistent.json"
    orch = _make_orch(lambda inp, prog: Ok(inp))

    # First REPL instance defines a macro
    repl1, _ = _make_repl(orch, session_path=path)
    repl1._session.define_macro("saved_macro", "upper")
    repl1._session.save(path)

    # Second REPL instance loads the saved session
    repl2, _ = _make_repl(orch, session_path=path)
    assert "saved_macro" in repl2._session.macro_names()
