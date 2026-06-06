"""Structural transformation plugins (sort, chunk, repeat, etc.)."""

from __future__ import annotations
from stratum.core.result import Err, Ok, Result
from stratum.plugins.base import ParameterDef, TransformPlugin


class SortPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "sort"
    @property
    def description(self) -> str: return "Sort words alphabetically."
    @property
    def category(self) -> str: return "structure"
    @property
    def parameters(self):
        return [ParameterDef("reverse", "boolean", required=False, default=False)]
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        rev = bool(kwargs.get("reverse", False))
        return Ok(" ".join(sorted(input_text.split(), reverse=rev)))


class UniquePlugin(TransformPlugin):
    @property
    def name(self) -> str: return "unique"
    @property
    def description(self) -> str: return "Remove duplicate words, preserving first occurrence."
    @property
    def category(self) -> str: return "structure"
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        seen: set[str] = set()
        words = []
        for w in input_text.split():
            if w not in seen:
                seen.add(w)
                words.append(w)
        return Ok(" ".join(words))


class ChunkPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "chunk"
    @property
    def description(self) -> str: return "Split string into chunks of N characters, separated by spaces."
    @property
    def category(self) -> str: return "structure"
    @property
    def parameters(self):
        return [ParameterDef("size", "integer", required=False, default=4, description="Chunk size")]
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        size = max(1, int(kwargs.get("size", 4)))
        chunks = [input_text[i:i+size] for i in range(0, len(input_text), size)]
        return Ok(" ".join(chunks))


class RepeatPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "repeat"
    @property
    def description(self) -> str: return "Repeat the string N times."
    @property
    def category(self) -> str: return "structure"
    @property
    def parameters(self):
        return [ParameterDef("times", "integer", required=False, default=2)]
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        n = max(0, int(kwargs.get("times", 2)))
        return Ok(input_text * n)


class WordCountPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "word_count"
    @property
    def description(self) -> str: return "Replace the string with its word count."
    @property
    def category(self) -> str: return "analysis"
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        return Ok(str(len(input_text.split())))


class CharCountPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "char_count"
    @property
    def description(self) -> str: return "Replace the string with its character count."
    @property
    def category(self) -> str: return "analysis"
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        return Ok(str(len(input_text)))


class SplitPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "split"
    @property
    def description(self) -> str: return "Split on a delimiter and re-join with spaces."
    @property
    def category(self) -> str: return "structure"
    @property
    def parameters(self):
        return [ParameterDef("sep", "string", required=False, default=",")]
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        sep = kwargs.get("sep", ",")
        return Ok(" ".join(p.strip() for p in input_text.split(sep)))


class JoinPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "join"
    @property
    def description(self) -> str: return "Join words with a delimiter."
    @property
    def category(self) -> str: return "structure"
    @property
    def parameters(self):
        return [ParameterDef("sep", "string", required=False, default=",")]
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        sep = kwargs.get("sep", ",")
        return Ok(sep.join(input_text.split()))
