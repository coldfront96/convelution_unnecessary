"""Padding, alignment, and trimming plugins."""

from __future__ import annotations
from stratum.core.result import Err, Ok, Result
from stratum.plugins.base import ParameterDef, TransformPlugin


class PadLeftPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "pad_left"
    @property
    def description(self) -> str: return "Left-pad to a target width."
    @property
    def category(self) -> str: return "padding"
    @property
    def parameters(self):
        return [
            ParameterDef("width", "integer", required=False, default=20),
            ParameterDef("char", "string", required=False, default=" "),
        ]
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        width = int(kwargs.get("width", 20))
        char = str(kwargs.get("char", " "))[:1] or " "
        return Ok(input_text.rjust(width, char))


class PadRightPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "pad_right"
    @property
    def description(self) -> str: return "Right-pad to a target width."
    @property
    def category(self) -> str: return "padding"
    @property
    def parameters(self):
        return [
            ParameterDef("width", "integer", required=False, default=20),
            ParameterDef("char", "string", required=False, default=" "),
        ]
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        width = int(kwargs.get("width", 20))
        char = str(kwargs.get("char", " "))[:1] or " "
        return Ok(input_text.ljust(width, char))


class CenterPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "center"
    @property
    def description(self) -> str: return "Center-align to a target width."
    @property
    def category(self) -> str: return "padding"
    @property
    def parameters(self):
        return [
            ParameterDef("width", "integer", required=False, default=20),
            ParameterDef("char", "string", required=False, default=" "),
        ]
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        width = int(kwargs.get("width", 20))
        char = str(kwargs.get("char", " "))[:1] or " "
        return Ok(input_text.center(width, char))


class TruncatePlugin(TransformPlugin):
    @property
    def name(self) -> str: return "truncate"
    @property
    def description(self) -> str: return "Truncate to N characters."
    @property
    def category(self) -> str: return "padding"
    @property
    def parameters(self):
        return [
            ParameterDef("length", "integer", required=False, default=80),
            ParameterDef("ellipsis", "string", required=False, default="..."),
        ]
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        length = int(kwargs.get("length", 80))
        ellipsis = str(kwargs.get("ellipsis", "..."))
        if len(input_text) <= length:
            return Ok(input_text)
        cut = max(0, length - len(ellipsis))
        return Ok(input_text[:cut] + ellipsis)


class TrimPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "trim"
    @property
    def description(self) -> str: return "Strip leading and trailing whitespace."
    @property
    def category(self) -> str: return "padding"
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        return Ok(input_text.strip())


class StripPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "strip"
    @property
    def description(self) -> str: return "Strip a specific character from both ends."
    @property
    def category(self) -> str: return "padding"
    @property
    def parameters(self):
        return [ParameterDef("char", "string", required=False, default=" ")]
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        char = str(kwargs.get("char", " "))
        return Ok(input_text.strip(char))
