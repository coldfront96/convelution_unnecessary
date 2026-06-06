"""Miscellaneous utility plugins."""

from __future__ import annotations
from stratum.core.result import Ok, Result
from stratum.plugins.base import ParameterDef, TransformPlugin


class IdentityPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "identity"
    @property
    def description(self) -> str: return "Return the input unchanged."
    @property
    def category(self) -> str: return "misc"
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        return Ok(input_text)


class AppendPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "append"
    @property
    def description(self) -> str: return "Append a string to the end."
    @property
    def category(self) -> str: return "misc"
    @property
    def parameters(self):
        return [ParameterDef("suffix", "string", required=False, default="")]
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        return Ok(input_text + str(kwargs.get("suffix", "")))


class PrependPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "prepend"
    @property
    def description(self) -> str: return "Prepend a string to the beginning."
    @property
    def category(self) -> str: return "misc"
    @property
    def parameters(self):
        return [ParameterDef("prefix", "string", required=False, default="")]
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        return Ok(str(kwargs.get("prefix", "")) + input_text)


class ReplacePlugin(TransformPlugin):
    @property
    def name(self) -> str: return "replace"
    @property
    def description(self) -> str: return "Replace occurrences of a substring."
    @property
    def category(self) -> str: return "misc"
    @property
    def parameters(self):
        return [
            ParameterDef("old", "string", required=False, default=""),
            ParameterDef("new", "string", required=False, default=""),
        ]
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        old = str(kwargs.get("old", ""))
        new = str(kwargs.get("new", ""))
        return Ok(input_text.replace(old, new))


class SlicePlugin(TransformPlugin):
    @property
    def name(self) -> str: return "slice"
    @property
    def description(self) -> str: return "Extract a substring by start and end index."
    @property
    def category(self) -> str: return "misc"
    @property
    def parameters(self):
        return [
            ParameterDef("start", "integer", required=False, default=0),
            ParameterDef("end", "integer", required=False, default=-1),
        ]
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        start = int(kwargs.get("start", 0))
        end   = int(kwargs.get("end", -1))
        if end == -1:
            return Ok(input_text[start:])
        return Ok(input_text[start:end])


class UpperFirstPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "upper_first"
    @property
    def description(self) -> str: return "Uppercase only the first character."
    @property
    def category(self) -> str: return "case"
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        if not input_text:
            return Ok(input_text)
        return Ok(input_text[0].upper() + input_text[1:])
