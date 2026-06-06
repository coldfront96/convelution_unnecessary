"""
REPL session state with persistence.

Holds macro definitions, the current working string, and the input
history of the current session. Serialises to / deserialises from JSON
so state survives restarts.

Pattern: Memento — snapshot() produces an immutable copy; restore()
         resets the session from one; used for undo in the REPL loop.
"""

from __future__ import annotations

import json
import logging
from copy import deepcopy
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class MacroEntry:
    name: str
    source: str  # the RHS of "macro name = <source>"


@dataclass
class SessionSnapshot:
    """Immutable point-in-time copy of session state (Memento)."""
    current_input: str
    macros: List[MacroEntry]


@dataclass
class ReplSession:
    """
    Mutable session state.

    current_input  — the "working string" that expressions are applied to
    macros         — user-defined macros, ordered (later defs can reference earlier)
    _history       — raw input lines (not the same as transformation history)
    """

    current_input: str = ""
    macros: List[MacroEntry] = field(default_factory=list)
    _history: List[str] = field(default_factory=list)

    # ---- macro management ---------------------------------------------------

    def define_macro(self, name: str, source: str) -> None:
        # Replace if already defined
        for i, m in enumerate(self.macros):
            if m.name == name:
                self.macros[i] = MacroEntry(name=name, source=source)
                return
        self.macros.append(MacroEntry(name=name, source=source))

    def remove_macro(self, name: str) -> bool:
        before = len(self.macros)
        self.macros = [m for m in self.macros if m.name != name]
        return len(self.macros) < before

    def get_macro(self, name: str) -> Optional[MacroEntry]:
        for m in self.macros:
            if m.name == name:
                return m
        return None

    def macro_names(self) -> List[str]:
        return [m.name for m in self.macros]

    # ---- preamble -----------------------------------------------------------

    def build_preamble(self) -> str:
        """
        Build an STL preamble that defines all session macros.

        Result looks like:  macro a = upper; macro b = lower;
        Intended to be prepended to user expressions before execution.
        """
        if not self.macros:
            return ""
        parts = [f"macro {m.name} = {m.source}" for m in self.macros]
        return "; ".join(parts) + "; "

    def wrap_program(self, user_expr: str) -> str:
        """Return preamble + user_expr ready for the orchestrator."""
        return self.build_preamble() + user_expr

    # ---- history ------------------------------------------------------------

    def push_history(self, line: str) -> None:
        if line.strip():
            self._history.append(line)

    def recent_history(self, n: int = 20) -> List[str]:
        return self._history[-n:]

    # ---- clear --------------------------------------------------------------

    def clear_macros(self) -> None:
        self.macros.clear()

    def reset(self) -> None:
        self.current_input = ""
        self.macros.clear()
        self._history.clear()

    # ---- Memento ------------------------------------------------------------

    def snapshot(self) -> SessionSnapshot:
        return SessionSnapshot(
            current_input=self.current_input,
            macros=deepcopy(self.macros),
        )

    def restore(self, snap: SessionSnapshot) -> None:
        self.current_input = snap.current_input
        self.macros = deepcopy(snap.macros)

    # ---- persistence --------------------------------------------------------

    @staticmethod
    def default_path() -> Path:
        config_dir = Path.home() / ".config" / "stratum"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir / "repl_session.json"

    def save(self, path: Optional[Path] = None) -> None:
        target = path or self.default_path()
        data = {
            "current_input": self.current_input,
            "macros": [{"name": m.name, "source": m.source} for m in self.macros],
        }
        target.write_text(json.dumps(data, indent=2))
        logger.debug("REPL session saved to %s", target)

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "ReplSession":
        source = path or cls.default_path()
        if not source.exists():
            return cls()
        try:
            data = json.loads(source.read_text())
            session = cls(current_input=data.get("current_input", ""))
            for m in data.get("macros", []):
                session.macros.append(MacroEntry(name=m["name"], source=m["source"]))
            logger.debug("REPL session loaded from %s (%d macros)", source, len(session.macros))
            return session
        except Exception as exc:
            logger.warning("could not load REPL session from %s: %s", source, exc)
            return cls()
