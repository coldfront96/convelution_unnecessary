"""Analysis plugins — inspect properties of the string."""

from __future__ import annotations
from stratum.core.result import Ok, Result
from stratum.plugins.base import ParameterDef, TransformPlugin


class LengthPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "length"
    @property
    def description(self) -> str: return "Return the length of the string as a string."
    @property
    def category(self) -> str: return "analysis"
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        return Ok(str(len(input_text)))


class IsUpperPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "is_upper"
    @property
    def description(self) -> str: return "Return '1' if all alphabetic chars are uppercase, else '0'."
    @property
    def category(self) -> str: return "analysis"
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        return Ok("1" if input_text.isupper() else "0")


class IsLowerPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "is_lower"
    @property
    def description(self) -> str: return "Return '1' if all alphabetic chars are lowercase, else '0'."
    @property
    def category(self) -> str: return "analysis"
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        return Ok("1" if input_text.islower() else "0")


class IsNumericPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "is_numeric"
    @property
    def description(self) -> str: return "Return '1' if the string is numeric, else '0'."
    @property
    def category(self) -> str: return "analysis"
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        return Ok("1" if input_text.strip().isnumeric() else "0")


class CountPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "count"
    @property
    def description(self) -> str: return "Count occurrences of a substring."
    @property
    def category(self) -> str: return "analysis"
    @property
    def parameters(self):
        return [ParameterDef("substring", "string", required=False, default=" ")]
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        sub = str(kwargs.get("substring", " "))
        return Ok(str(input_text.count(sub)))


class ContainsPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "contains"
    @property
    def description(self) -> str: return "Return '1' if string contains substring, else '0'."
    @property
    def category(self) -> str: return "analysis"
    @property
    def parameters(self):
        return [ParameterDef("substring", "string", required=False, default="")]
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        sub = str(kwargs.get("substring", ""))
        return Ok("1" if sub in input_text else "0")
