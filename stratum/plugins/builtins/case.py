"""Case-transformation plugins."""

from __future__ import annotations
from stratum.core.result import Ok, Result
from stratum.plugins.base import TransformPlugin


class UpperPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "upper"
    @property
    def description(self) -> str: return "Convert all characters to uppercase."
    @property
    def category(self) -> str: return "case"
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        return Ok(input_text.upper())


class LowerPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "lower"
    @property
    def description(self) -> str: return "Convert all characters to lowercase."
    @property
    def category(self) -> str: return "case"
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        return Ok(input_text.lower())


class TitlePlugin(TransformPlugin):
    @property
    def name(self) -> str: return "title"
    @property
    def description(self) -> str: return "Title-case every word."
    @property
    def category(self) -> str: return "case"
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        return Ok(input_text.title())


class SwapcasePlugin(TransformPlugin):
    @property
    def name(self) -> str: return "swapcase"
    @property
    def description(self) -> str: return "Swap upper↔lower for each character."
    @property
    def category(self) -> str: return "case"
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        return Ok(input_text.swapcase())


class CapitalizePlugin(TransformPlugin):
    @property
    def name(self) -> str: return "capitalize"
    @property
    def description(self) -> str: return "Capitalize first character, lowercase rest."
    @property
    def category(self) -> str: return "case"
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        return Ok(input_text.capitalize())
