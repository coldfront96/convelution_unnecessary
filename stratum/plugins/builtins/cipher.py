"""Cipher and encoding plugins."""

from __future__ import annotations
import base64
from stratum.core.result import Err, Ok, Result
from stratum.plugins.base import ParameterDef, TransformPlugin


class Rot13Plugin(TransformPlugin):
    @property
    def name(self) -> str: return "rot13"
    @property
    def description(self) -> str: return "Apply ROT-13 substitution cipher."
    @property
    def category(self) -> str: return "cipher"
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        return Ok(input_text.translate(str.maketrans(
            "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
            "NOPQRSTUVWXYZABCDEFGHIJKLMnopqrstuvwxyzabcdefghijklm",
        )))


class CaesarPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "caesar"
    @property
    def description(self) -> str: return "Caesar cipher with configurable shift."
    @property
    def category(self) -> str: return "cipher"
    @property
    def parameters(self):
        return [ParameterDef("shift", "integer", required=False, default=3, description="Shift amount")]

    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        shift = int(kwargs.get("shift", 3)) % 26
        result = []
        for ch in input_text:
            if ch.isalpha():
                base = ord('A') if ch.isupper() else ord('a')
                result.append(chr((ord(ch) - base + shift) % 26 + base))
            else:
                result.append(ch)
        return Ok("".join(result))


class Base64EncodePlugin(TransformPlugin):
    @property
    def name(self) -> str: return "base64_encode"
    @property
    def description(self) -> str: return "Encode input as base64."
    @property
    def category(self) -> str: return "cipher"
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        return Ok(base64.b64encode(input_text.encode()).decode())


class Base64DecodePlugin(TransformPlugin):
    @property
    def name(self) -> str: return "base64_decode"
    @property
    def description(self) -> str: return "Decode base64 input."
    @property
    def category(self) -> str: return "cipher"
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        try:
            return Ok(base64.b64decode(input_text.encode()).decode())
        except Exception as exc:
            return Err(f"base64_decode: {exc}")


class ReversePlugin(TransformPlugin):
    @property
    def name(self) -> str: return "reverse"
    @property
    def description(self) -> str: return "Reverse the entire string."
    @property
    def category(self) -> str: return "structure"
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        return Ok(input_text[::-1])


class MirrorPlugin(TransformPlugin):
    @property
    def name(self) -> str: return "mirror"
    @property
    def description(self) -> str: return "Append the reversed string to itself."
    @property
    def category(self) -> str: return "structure"
    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        return Ok(input_text + input_text[::-1])
