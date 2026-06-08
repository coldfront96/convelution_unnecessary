"""
Drift strategies — apply after the final decode round.

The result is CLOSE to the original but not identical, like a message
passed through too many hands. The drift is deterministic (same salt =
same substitutions) so re-encoding and re-decoding always drifts to the
same almost-right output.

Pattern: Strategy (DriftStrategy ABC with three concrete implementations).
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod


class DriftStrategy(ABC):
    """Strategy: defines how text mutates after final decoding."""

    @abstractmethod
    def drift(self, text: str, seed: int) -> str: ...

    @abstractmethod
    def count_drifted(self, original: str, drifted: str) -> int: ...


# Phonetically similar letter groups — substituting within a group sounds almost right
_PHONETIC_TABLE: dict[str, list[str]] = {
    "a":    ["e", "o"],
    "e":    ["a", "i"],
    "i":    ["e", "y"],
    "o":    ["u", "0"],
    "u":    ["oo", "o"],
    "or":   ["ir", "er"],
    "er":   ["ar", "ur"],
    "ar":   ["er", "or"],
    "th":   ["d", "t"],
    "wh":   ["w"],
    "ck":   ["k", "c"],
    "ph":   ["f"],
    "tion": ["shun"],
    "gh":   ["f", "g"],
}

# Visually similar single-char substitutions
_VISUAL_TABLE: dict[str, str] = {
    "a": "@",
    "e": "3",
    "i": "1",
    "o": "0",
    "l": "1",
    "s": "5",
    "b": "6",
    "g": "9",
    "t": "+",
}


class PhoneticDrift(DriftStrategy):
    """Substitutes phonetically similar sequences at a given rate."""

    def __init__(self, drift_rate: float = 0.15) -> None:
        self._rate = drift_rate

    def drift(self, text: str, seed: int) -> str:
        rng = random.Random(seed)
        result: list[str] = []
        i = 0
        while i < len(text):
            substituted = False
            for length in (4, 3, 2, 1):
                chunk = text[i:i + length].lower()
                if chunk in _PHONETIC_TABLE and rng.random() < self._rate:
                    sub = rng.choice(_PHONETIC_TABLE[chunk])
                    result.append(sub)
                    i += length
                    substituted = True
                    break
            if not substituted:
                result.append(text[i])
                i += 1
        return ''.join(result)

    def count_drifted(self, original: str, drifted: str) -> int:
        return sum(a != b for a, b in zip(original, drifted)) + abs(len(original) - len(drifted))


class VisualDrift(DriftStrategy):
    """Substitutes visually similar characters at a lower rate."""

    def __init__(self, drift_rate: float = 0.08) -> None:
        self._rate = drift_rate

    def drift(self, text: str, seed: int) -> str:
        rng = random.Random(seed ^ 0x1234)
        result: list[str] = []
        for c in text:
            sub = _VISUAL_TABLE.get(c.lower())
            if sub and rng.random() < self._rate:
                result.append(sub if c.islower() else sub.upper())
            else:
                result.append(c)
        return ''.join(result)

    def count_drifted(self, original: str, drifted: str) -> int:
        return sum(a != b for a, b in zip(original, drifted))


class CompoundDrift(DriftStrategy):
    """
    Applies phonetic drift first, then visual drift.
    The compound effect produces text that sounds AND looks almost right
    but is subtly, irreversibly wrong in a deterministic way.
    """

    def __init__(self, drift_rate: float = 0.15) -> None:
        self._phonetic = PhoneticDrift(drift_rate=drift_rate)
        self._visual = VisualDrift(drift_rate=drift_rate * 0.5)

    def drift(self, text: str, seed: int) -> str:
        after_phonetic = self._phonetic.drift(text, seed)
        return self._visual.drift(after_phonetic, seed ^ 0x5678)

    def count_drifted(self, original: str, drifted: str) -> int:
        return sum(a != b for a, b in zip(original, drifted)) + abs(len(original) - len(drifted))
