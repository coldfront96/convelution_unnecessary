"""
Six reversible ciphers operating on printable ASCII (32–126).

Each cipher takes (text: str, key: int) and is perfectly invertible:
    decipher(encipher(text, key), key) == text

The chain is applied in round-number order, cycling through all six.
This is the Chain of Responsibility pattern: each cipher handles its
designated round and passes modified text through.

Pattern: Template Method (CipherBase defines the encipher/decipher contract),
         Chain of Responsibility (CIPHER_CHAIN drives round dispatch).
"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod

PRINTABLE_LOW = 32    # space
PRINTABLE_HIGH = 126  # ~
PRINTABLE_RANGE = PRINTABLE_HIGH - PRINTABLE_LOW + 1  # 95


def _in_range(c: str) -> bool:
    return PRINTABLE_LOW <= ord(c) <= PRINTABLE_HIGH


def _shift(c: str, amount: int) -> str:
    if not _in_range(c):
        return c
    return chr(PRINTABLE_LOW + (ord(c) - PRINTABLE_LOW + amount) % PRINTABLE_RANGE)


class CipherBase(ABC):
    """Template Method: subclasses implement encipher/decipher within printable ASCII."""

    @abstractmethod
    def encipher(self, text: str, key: int) -> str: ...

    @abstractmethod
    def decipher(self, text: str, key: int) -> str: ...

    def _make_permutation(self, seed: int, size: int) -> list[int]:
        rng = random.Random(seed)
        perm = list(range(size))
        rng.shuffle(perm)
        return perm

    @staticmethod
    def _invert_permutation(perm: list[int]) -> list[int]:
        inv = [0] * len(perm)
        for i, p in enumerate(perm):
            inv[p] = i
        return inv


class RotCipher(CipherBase):
    """Shift every printable char by a key-derived constant."""

    def _shift_amount(self, key: int) -> int:
        return (key * 7 + 13) % PRINTABLE_RANGE

    def encipher(self, text: str, key: int) -> str:
        s = self._shift_amount(key)
        return ''.join(_shift(c, s) for c in text)

    def decipher(self, text: str, key: int) -> str:
        s = self._shift_amount(key)
        return ''.join(_shift(c, -s) for c in text)


class VigenereCipher(CipherBase):
    """Polyalphabetic shift: each character gets a different key-derived shift."""

    def _key_stream(self, key: int, length: int) -> list[int]:
        rng = random.Random(key ^ 0xDEAD)
        return [rng.randint(1, PRINTABLE_RANGE - 1) for _ in range(length)]

    def encipher(self, text: str, key: int) -> str:
        ks = self._key_stream(key, len(text))
        return ''.join(_shift(c, ks[i]) if _in_range(c) else c for i, c in enumerate(text))

    def decipher(self, text: str, key: int) -> str:
        ks = self._key_stream(key, len(text))
        return ''.join(_shift(c, -ks[i]) if _in_range(c) else c for i, c in enumerate(text))


class TranspositionCipher(CipherBase):
    """Permute character positions using a seeded Fisher-Yates shuffle."""

    def encipher(self, text: str, key: int) -> str:
        chars = list(text)
        perm = self._make_permutation(key ^ 0xBEEF, len(chars))
        return ''.join(chars[p] for p in perm)

    def decipher(self, text: str, key: int) -> str:
        chars = list(text)
        perm = self._make_permutation(key ^ 0xBEEF, len(chars))
        inv = self._invert_permutation(perm)
        return ''.join(chars[p] for p in inv)


class SubstitutionCipher(CipherBase):
    """Full alphabet substitution: every printable char maps to a different one."""

    def _make_tables(self, key: int) -> tuple[dict[int, int], dict[int, int]]:
        printable = list(range(PRINTABLE_LOW, PRINTABLE_HIGH + 1))
        perm_idx = self._make_permutation(key ^ 0xCAFE, len(printable))
        perm = [printable[i] for i in perm_idx]
        encode = {orig: sub for orig, sub in zip(printable, perm)}
        decode = {sub: orig for orig, sub in zip(printable, perm)}
        return encode, decode

    def encipher(self, text: str, key: int) -> str:
        enc, _ = self._make_tables(key)
        return ''.join(chr(enc.get(ord(c), ord(c))) for c in text)

    def decipher(self, text: str, key: int) -> str:
        _, dec = self._make_tables(key)
        return ''.join(chr(dec.get(ord(c), ord(c))) for c in text)


class InterleaveCipher(CipherBase):
    """Split string into even/odd indexed characters and concatenate (self-described interleave)."""

    def encipher(self, text: str, key: int) -> str:
        # Read even-indexed positions first, then odd — looks like random reordering
        offset = (key * 3 + 7) % max(1, len(text))
        rotated = text[offset:] + text[:offset]
        return rotated[0::2] + rotated[1::2]

    def decipher(self, text: str, key: int) -> str:
        offset = (key * 3 + 7) % max(1, len(text))
        n = len(text)
        even_len = (n + 1) // 2
        even = text[:even_len]
        odd = text[even_len:]
        result = [''] * n
        for i, c in enumerate(even):
            result[i * 2] = c
        for i, c in enumerate(odd):
            result[i * 2 + 1] = c
        unrotated = ''.join(result)
        return unrotated[-offset:] + unrotated[:-offset] if offset else unrotated


class ScrambleCipher(CipherBase):
    """Position-aware scramble: each character shifted by key + position-derived amount."""

    def _shifts(self, key: int, length: int) -> list[int]:
        rng = random.Random(key ^ 0xF00D)
        return [rng.randint(0, PRINTABLE_RANGE - 1) for _ in range(length)]

    def encipher(self, text: str, key: int) -> str:
        shifts = self._shifts(key, len(text))
        return ''.join(
            _shift(c, shifts[i] + i) if _in_range(c) else c
            for i, c in enumerate(text)
        )

    def decipher(self, text: str, key: int) -> str:
        shifts = self._shifts(key, len(text))
        return ''.join(
            _shift(c, -(shifts[i] + i)) if _in_range(c) else c
            for i, c in enumerate(text)
        )


# Chain of Responsibility: round % 6 selects the cipher for that round
CIPHER_CHAIN: list[type[CipherBase]] = [
    RotCipher,
    VigenereCipher,
    TranspositionCipher,
    SubstitutionCipher,
    InterleaveCipher,
    ScrambleCipher,
]
