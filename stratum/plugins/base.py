"""
Plugin base class and parameter descriptor.

Every built-in transform is a plugin. The VM knows nothing about what
'upper' or 'rot13' mean — it calls PluginRegistry.invoke() and the
registry dispatches to the right plugin instance.

Pattern: Template Method (execute_validated calls validate_params then execute),
         Abstract Factory (PluginFactory creates plugins from config).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from stratum.core.result import Err, Ok, Result


class PluginError(Exception):
    pass


@dataclass(frozen=True)
class ParameterDef:
    """Descriptor for a single plugin parameter."""
    name: str
    type: str           # "string" | "integer" | "float" | "boolean"
    required: bool = False
    default: Any = None
    description: str = ""
    choices: Optional[List[Any]] = None

    def validate(self, value: Any) -> tuple[Any, Optional[str]]:
        """Coerce and validate. Returns (coerced_value, error_or_None)."""
        if value is None:
            if self.required:
                return None, f"parameter '{self.name}' is required"
            return self.default, None

        try:
            if self.type == "string":
                coerced = str(value)
            elif self.type == "integer":
                coerced = int(value)
            elif self.type == "float":
                coerced = float(value)
            elif self.type == "boolean":
                if isinstance(value, bool):
                    coerced = value
                elif isinstance(value, int):
                    coerced = bool(value)
                elif isinstance(value, str):
                    coerced = value.lower() in ("true", "1", "yes")
                else:
                    return None, f"parameter '{self.name}': cannot coerce {type(value).__name__} to boolean"
            else:
                coerced = value
        except (ValueError, TypeError) as exc:
            return None, f"parameter '{self.name}': {exc}"

        if self.choices is not None and coerced not in self.choices:
            return None, f"parameter '{self.name}': {coerced!r} not in {self.choices}"

        return coerced, None


class TransformPlugin(ABC):
    """
    Base class for all STRATUM transform plugins.

    Template Method pattern: execute_validated handles parameter
    validation and delegates to the abstract execute() method.

    Subclasses must implement:
        name        — unique identifier used in STL programs
        description — human-readable description
        parameters  — list of ParameterDef
        execute     — the actual transformation
    """

    @property
    @abstractmethod
    def name(self) -> str: ...

    @property
    @abstractmethod
    def description(self) -> str: ...

    @property
    def parameters(self) -> List[ParameterDef]:
        return []

    @property
    def category(self) -> str:
        return "general"

    @abstractmethod
    def execute(self, input_text: str, **kwargs: Any) -> Result[str, str]: ...

    def execute_validated(
        self, input_text: str, **raw_kwargs: Any
    ) -> Result[str, str]:
        """Validate parameters, then call execute(). Template Method."""
        validated: Dict[str, Any] = {}
        for param_def in self.parameters:
            raw = raw_kwargs.get(param_def.name)
            coerced, error = param_def.validate(raw)
            if error:
                return Err(f"[{self.name}] {error}")
            validated[param_def.name] = coerced

        # Pass through any extra kwargs (for forward-compat)
        for key, val in raw_kwargs.items():
            if key not in validated:
                validated[key] = val

        try:
            return self.execute(input_text, **validated)
        except Exception as exc:
            return Err(f"[{self.name}] unexpected error: {exc}")

    def __repr__(self) -> str:
        param_names = [p.name for p in self.parameters]
        return f"Plugin({self.name!r}, params={param_names})"


class PluginFactory(ABC):
    """
    Abstract Factory for creating plugin instances.
    Concrete implementations can load from files, configs, or hard-code.

    Pattern: Abstract Factory.
    """

    @abstractmethod
    def create_plugins(self) -> List[TransformPlugin]: ...


class BuiltinPluginFactory(PluginFactory):
    """
    Factory that creates all built-in plugins.
    Importing is deferred to avoid circular imports at module load time.
    """

    def create_plugins(self) -> List[TransformPlugin]:
        from stratum.plugins.builtins.case import (
            UpperPlugin, LowerPlugin, TitlePlugin, SwapcasePlugin, CapitalizePlugin,
        )
        from stratum.plugins.builtins.cipher import (
            Rot13Plugin, CaesarPlugin, Base64EncodePlugin, Base64DecodePlugin,
            ReversePlugin, MirrorPlugin,
        )
        from stratum.plugins.builtins.structure import (
            SortPlugin, UniquePlugin, ChunkPlugin, RepeatPlugin,
            WordCountPlugin, CharCountPlugin, SplitPlugin, JoinPlugin,
        )
        from stratum.plugins.builtins.padding import (
            PadLeftPlugin, PadRightPlugin, CenterPlugin, TruncatePlugin,
            TrimPlugin, StripPlugin,
        )
        from stratum.plugins.builtins.analysis import (
            LengthPlugin, IsUpperPlugin, IsLowerPlugin, IsNumericPlugin,
            CountPlugin, ContainsPlugin,
        )
        from stratum.plugins.builtins.misc import (
            IdentityPlugin, AppendPlugin, PrependPlugin, ReplacePlugin,
            SlicePlugin, UpperFirstPlugin,
        )
        return [
            UpperPlugin(), LowerPlugin(), TitlePlugin(), SwapcasePlugin(), CapitalizePlugin(),
            Rot13Plugin(), CaesarPlugin(), Base64EncodePlugin(), Base64DecodePlugin(),
            ReversePlugin(), MirrorPlugin(),
            SortPlugin(), UniquePlugin(), ChunkPlugin(), RepeatPlugin(),
            WordCountPlugin(), CharCountPlugin(), SplitPlugin(), JoinPlugin(),
            PadLeftPlugin(), PadRightPlugin(), CenterPlugin(), TruncatePlugin(),
            TrimPlugin(), StripPlugin(),
            LengthPlugin(), IsUpperPlugin(), IsLowerPlugin(), IsNumericPlugin(),
            CountPlugin(), ContainsPlugin(),
            IdentityPlugin(), AppendPlugin(), PrependPlugin(), ReplacePlugin(),
            SlicePlugin(), UpperFirstPlugin(),
        ]
