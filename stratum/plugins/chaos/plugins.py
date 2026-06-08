"""
Chaos plugins: chaos_encode and chaos_decode.

chaos_encode  — scramble text through a random hidden number of rounds
chaos_decode  — peel one round; keep calling until ~DECODED~ appears

Both plugins share one ChaosCodec instance (created by ChaosPluginFactory)
so they use the same configuration and RNG.

Pattern: Abstract Factory (ChaosPluginFactory produces paired plugins),
         Template Method (inherited from TransformPlugin.execute_validated).
"""

from __future__ import annotations

from typing import List

from stratum.core.result import Err, Ok, Result
from stratum.plugins.base import PluginFactory, TransformPlugin
from stratum.plugins.chaos.codec import ChaosCodec, ChaosDecodeError


class ChaosEncodePlugin(TransformPlugin):
    """
    Scramble input through an unknown number of cipher rounds.
    The output looks like random printable ASCII — no hints are given
    about how many chaos_decode calls will be required to recover
    (an approximate version of) the original.
    """

    def __init__(self, codec: ChaosCodec) -> None:
        self._codec = codec

    @property
    def name(self) -> str:
        return "chaos_encode"

    @property
    def description(self) -> str:
        return (
            "Encode through a random number of hidden scramble rounds. "
            "Use chaos_decode repeatedly to unwrap — you won't know how many times."
        )

    @property
    def category(self) -> str:
        return "chaos"

    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        try:
            return Ok(self._codec.encode(input_text))
        except ChaosDecodeError as exc:
            return Err(f"[chaos_encode] {exc}")


class ChaosDecodePlugin(TransformPlugin):
    """
    Peel exactly one layer of chaos encoding.
    Keep applying until the output starts with '~DECODED~ ' — that is
    the only signal that all layers have been removed (with slight drift).
    There is no countdown. There is no progress indicator. Good luck.
    """

    def __init__(self, codec: ChaosCodec) -> None:
        self._codec = codec

    @property
    def name(self) -> str:
        return "chaos_decode"

    @property
    def description(self) -> str:
        return (
            "Peel one chaos layer. Repeat until output begins with '~DECODED~ '. "
            "The number of required repetitions is unknown and random."
        )

    @property
    def category(self) -> str:
        return "chaos"

    def execute(self, input_text: str, **kwargs) -> Result[str, str]:
        try:
            result, _ = self._codec.decode(input_text)
            return Ok(result)
        except ChaosDecodeError as exc:
            return Err(f"[chaos_decode] {exc}")


class ChaosPluginFactory(PluginFactory):
    """
    Creates chaos_encode and chaos_decode sharing one ChaosCodec.

    Pattern: Abstract Factory.
    """

    def __init__(
        self,
        min_rounds: int = 15,
        max_rounds: int = 50,
        drift_rate: float = 0.15,
    ) -> None:
        self._min_rounds = min_rounds
        self._max_rounds = max_rounds
        self._drift_rate = drift_rate

    def create_plugins(self) -> List[TransformPlugin]:
        codec = ChaosCodec(
            min_rounds=self._min_rounds,
            max_rounds=self._max_rounds,
            drift_rate=self._drift_rate,
        )
        return [
            ChaosEncodePlugin(codec),
            ChaosDecodePlugin(codec),
        ]
