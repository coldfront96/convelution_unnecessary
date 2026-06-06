"""
Interactive REPL for STRATUM.

Supports:
  - Evaluating STL expressions against a persistent working string
  - Defining named macros that survive session restarts
  - Dot-commands for session management
  - Line editing and command history via readline (stdlib)
  - Undo via Memento snapshots

Dot-commands:
  .help              Show this help
  .quit / .exit      Exit the REPL (saves session)
  .set <text>        Set the current working string
  .get               Print the current working string
  .macro <n>=<src>   Define a macro (e.g.  .macro shout = upper | append("!"))
  .macros            List all defined macros
  .delmacro <name>   Remove a macro
  .clear             Reset session state (keeps history)
  .undo              Undo the last transformation (Memento restore)
  .history [n]       Show last N lines of REPL input history (default 20)
  .save              Save session to disk now
  .typecheck <prog>  Type-check a program without executing it
  .disasm <prog>     Disassemble a program

Pattern: Command (each dot-command is handled as a distinct command dispatch),
         Memento (undo restores prior snapshot),
         Iterator (readline drives the input loop).
"""

from __future__ import annotations

import sys
import logging
from pathlib import Path
from typing import Any, List, Optional

from stratum.repl.session import ReplSession, SessionSnapshot

logger = logging.getLogger(__name__)

_BANNER = """
STRATUM REPL  (type .help for commands, .quit to exit)
Working string: {input!r}
{macro_info}"""

_HELP = """
STRATUM REPL — STL Interactive Evaluator

  <expression>          Apply STL expression to the working string.
                        Output becomes the new working string.

  .set <text>           Set the working string.
  .get                  Print the current working string.
  .macro <n> = <src>    Define a reusable macro (e.g. .macro shout = upper).
  .macros               List defined macros.
  .delmacro <name>      Remove a macro.
  .clear                Reset working string and macros.
  .undo                 Undo the last transformation.
  .history [n]          Show last n input lines (default 20).
  .typecheck <prog>     Type-check without executing.
  .disasm <prog>        Show bytecode disassembly.
  .save                 Save session to disk.
  .help                 Show this message.
  .quit / .exit         Exit.
""".strip()


class Repl:
    """
    The interactive REPL.

    *orchestrator* is the Facade used for all STL execution.
    *history_projection* is read for .history queries.
    *session_path* overrides the default persistence file.
    *output* allows redirecting output (useful in tests).
    """

    PROMPT = "stratum> "
    CONTINUE_PROMPT = "      .. "

    def __init__(
        self,
        orchestrator: Any,
        history_projection: Any,
        session_path: Optional[Path] = None,
        output=None,
    ) -> None:
        self._orch = orchestrator
        self._hist_proj = history_projection
        self._output = output or sys.stdout
        self._session_path = session_path
        self._session = ReplSession.load(session_path)
        self._undo_stack: List[SessionSnapshot] = []

    # ---- public entry point -------------------------------------------------

    def run(self) -> None:
        """Start the interactive loop. Blocks until user quits."""
        self._setup_readline()
        self._print_banner()
        try:
            self._loop()
        except (EOFError, KeyboardInterrupt):
            self._println("\n(interrupted)")
        finally:
            self._session.save(self._session_path)
            self._println("Session saved. Goodbye.")

    # ---- non-interactive execution (for testing) ----------------------------

    def execute_line(self, line: str) -> Optional[str]:
        """
        Execute a single line non-interactively and return the output or None.
        Side-effects (session mutation, undo stack) still apply.
        """
        return self._handle_line(line.strip())

    # ---- internal loop ------------------------------------------------------

    def _loop(self) -> None:
        while True:
            try:
                line = input(self.PROMPT).strip()
            except EOFError:
                raise
            if not line:
                continue
            self._session.push_history(line)
            result = self._handle_line(line)
            if result == "__quit__":
                break
            if result is not None:
                self._println(result)

    def _handle_line(self, line: str) -> Optional[str]:
        if not line:
            return None

        if line.startswith("."):
            return self._handle_dot_command(line)

        # Regular STL expression — save undo snapshot first
        self._push_undo()
        program = self._session.wrap_program(line)
        result = self._orch.transform(self._session.current_input, program)
        if result.is_ok():
            self._session.current_input = result.unwrap()
            return result.unwrap()
        else:
            # Restore undo (no state change on error)
            self._pop_undo()
            return f"Error: {result.unwrap_err()}"

    # ---- dot-commands -------------------------------------------------------

    def _handle_dot_command(self, line: str) -> Optional[str]:
        parts = line[1:].split(None, 1)  # strip leading dot, split on first space
        cmd = parts[0].lower() if parts else ""
        rest = parts[1] if len(parts) > 1 else ""

        if cmd in ("quit", "exit"):
            return "__quit__"

        if cmd == "help":
            return _HELP

        if cmd == "set":
            self._session.current_input = rest
            return f"Working string set to {rest!r}"

        if cmd == "get":
            return repr(self._session.current_input)

        if cmd == "macro":
            return self._cmd_define_macro(rest)

        if cmd == "macros":
            return self._cmd_list_macros()

        if cmd == "delmacro":
            name = rest.strip()
            if self._session.remove_macro(name):
                return f"Macro {name!r} removed."
            return f"No macro named {name!r}."

        if cmd == "clear":
            self._session.reset()
            return "Session cleared."

        if cmd == "undo":
            return self._cmd_undo()

        if cmd == "history":
            try:
                n = int(rest.strip()) if rest.strip() else 20
            except ValueError:
                n = 20
            lines = self._session.recent_history(n)
            if not lines:
                return "(no history)"
            return "\n".join(f"{i+1:3}. {l}" for i, l in enumerate(lines))

        if cmd == "save":
            self._session.save(self._session_path)
            return "Session saved."

        if cmd == "typecheck":
            if not rest:
                return "Usage: .typecheck <program>"
            r = self._orch.typecheck(self._session.wrap_program(rest))
            return r.unwrap() if r.is_ok() else f"Error: {r.unwrap_err()}"

        if cmd == "disasm":
            if not rest:
                return "Usage: .disasm <program>"
            r = self._orch.disassemble(self._session.wrap_program(rest))
            return r.unwrap() if r.is_ok() else f"Error: {r.unwrap_err()}"

        return f"Unknown command: .{cmd}  (type .help for commands)"

    def _cmd_define_macro(self, rest: str) -> str:
        # Expect "name = source" (spaces around = are optional)
        if "=" not in rest:
            return "Usage: .macro <name> = <program>"
        name, _, source = rest.partition("=")
        name = name.strip()
        source = source.strip()
        if not name or not name.isidentifier():
            return f"Invalid macro name: {name!r}"
        if not source:
            return "Macro source cannot be empty."
        # Quick validation: try to compile "macro name = source; identity"
        probe = f"macro {name} = {source}; identity"
        r = self._orch.transform("", probe)
        if r.is_err():
            return f"Invalid macro source: {r.unwrap_err()}"
        self._session.define_macro(name, source)
        return f"Macro {name!r} defined."

    def _cmd_list_macros(self) -> str:
        if not self._session.macros:
            return "(no macros defined)"
        lines = [f"  macro {m.name} = {m.source}" for m in self._session.macros]
        return "\n".join(lines)

    def _cmd_undo(self) -> str:
        if not self._undo_stack:
            return "Nothing to undo."
        snap = self._undo_stack.pop()
        self._session.restore(snap)
        return f"Undone. Working string: {self._session.current_input!r}"

    # ---- undo stack ---------------------------------------------------------

    def _push_undo(self) -> None:
        self._undo_stack.append(self._session.snapshot())
        if len(self._undo_stack) > 50:
            self._undo_stack.pop(0)

    def _pop_undo(self) -> None:
        if self._undo_stack:
            snap = self._undo_stack.pop()
            self._session.restore(snap)

    # ---- readline setup -----------------------------------------------------

    def _setup_readline(self) -> None:
        try:
            import readline  # noqa: F401  — side-effect: enables line editing
            readline.set_history_length(500)
        except ImportError:
            pass  # readline unavailable on Windows without pyreadline

    # ---- output helpers -----------------------------------------------------

    def _println(self, text: str) -> None:
        print(text, file=self._output)

    def _print_banner(self) -> None:
        macro_info = ""
        if self._session.macros:
            macro_info = f"Macros loaded: {', '.join(self._session.macro_names())}"
        self._println(_BANNER.format(
            input=self._session.current_input,
            macro_info=macro_info,
        ).strip())
