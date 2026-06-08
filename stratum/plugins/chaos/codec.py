"""
ChaosCodec — encodes text through a random number of scrambling rounds
and decodes one layer at a time.

The round count is hidden inside the encoded payload using a compact
8-character header that is visually indistinguishable from the scrambled
content (all printable ASCII, no visible structure).

Header format (8 printable chars, positions 0-7 of the encoded string):
  chars 0-1 : rounds_hi, rounds_lo  (base-94: rounds = hi*94 + lo, max 8835)
  chars 2-5 : 4 salt values derived from SHA-256 of the original text (mod 94)
  chars 6-7 : checksum hi/lo nibbles (XOR of bytes 0-5, stored as 2 nibbles)

Every value is in 0-93, stored as chr(33 + value) — printable, non-space,
indistinguishable from scrambled content.
"""

from __future__ import annotations

import hashlib
import random
from typing import Optional, Tuple

from stratum.plugins.chaos.ciphers import CIPHER_CHAIN
from stratum.plugins.chaos.drift import CompoundDrift

# ---------------------------------------------------------------------------
# Header constants
# ---------------------------------------------------------------------------

_HEADER_SIZE = 8
_H_LOW = 33      # '!'
_H_RANGE = 94    # '!' to '~'


def _v2c(v: int) -> str:
    """Encode a value in [0, 93] as a single printable char."""
    return chr(_H_LOW + (v % _H_RANGE))


def _c2v(c: str) -> int:
    """Decode a printable header char back to an integer in [0, 93]."""
    return ord(c) - _H_LOW


def _pack_header(rounds: int, salt: bytes) -> str:
    r_hi = rounds // _H_RANGE       # 0-93
    r_lo = rounds % _H_RANGE        # 0-93
    # Salt bytes are already reduced to 0-93 before being passed in
    s = [b % _H_RANGE for b in salt[:4]]
    raw = [r_hi, r_lo] + s          # 6 values, each in 0-93
    cksum = 0
    for v in raw:
        cksum ^= v                  # XOR of 7-bit values stays ≤ 127
    ck_hi = (cksum >> 4) & 0xF     # 0-15 — safe
    ck_lo = cksum & 0xF            # 0-15 — safe
    return ''.join(_v2c(v) for v in raw + [ck_hi, ck_lo])


def _unpack_header(header: str) -> Tuple[int, bytes]:
    if len(header) < _HEADER_SIZE:
        raise ChaosDecodeError("Input too short — is this chaos-encoded text?")
    raw = [_c2v(header[i]) for i in range(8)]
    r_hi, r_lo = raw[0], raw[1]
    s = raw[2:6]
    ck_hi_got, ck_lo_got = raw[6], raw[7]

    cksum = 0
    for v in raw[:6]:
        cksum ^= v
    if ck_hi_got != (cksum >> 4) & 0xF or ck_lo_got != cksum & 0xF:
        raise ChaosDecodeError(
            "Checksum mismatch — input doesn't appear to be chaos-encoded. "
            "Did you accidentally edit or truncate it?"
        )
    return r_hi * _H_RANGE + r_lo, bytes(s)


def _cipher_key(round_num: int, salt: bytes) -> int:
    return int.from_bytes(
        hashlib.sha256(salt + round_num.to_bytes(4, "big")).digest()[:4], "big"
    )


def _make_salt(text: str) -> bytes:
    """Derive a 4-byte salt from the text, with each byte reduced to [0, 93]."""
    raw = hashlib.sha256(text.encode("utf-8", errors="replace")).digest()[:4]
    return bytes(b % _H_RANGE for b in raw)


# ---------------------------------------------------------------------------
# Optional event bus hook
# ---------------------------------------------------------------------------

_event_bus: Optional[object] = None


def configure_event_bus(bus: object) -> None:
    global _event_bus
    _event_bus = bus


def _emit(event: object) -> None:
    if _event_bus is not None:
        try:
            _event_bus.publish(event)  # type: ignore[union-attr]
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main codec
# ---------------------------------------------------------------------------


class ChaosDecodeError(Exception):
    pass


DECODED_PREFIX = "~DECODED~ "


class ChaosCodec:
    """
    Encodes text through a random hidden number of scrambling rounds.
    Each call to decode() peels exactly one round.  The caller has no
    way to know how many rounds remain — the only signal of completion
    is the DECODED_PREFIX on the final result.
    """

    def __init__(
        self,
        min_rounds: int = 15,
        max_rounds: int = 50,
        drift_rate: float = 0.15,
        rng: Optional[random.Random] = None,
    ) -> None:
        if min_rounds > max_rounds:
            raise ValueError(f"min_rounds ({min_rounds}) must be ≤ max_rounds ({max_rounds})")
        self._min = min_rounds
        self._max = max_rounds
        self._drift = CompoundDrift(drift_rate=drift_rate)
        self._rng: random.Random = rng or random.SystemRandom()  # type: ignore[assignment]

    def encode(self, text: str) -> str:
        if not text:
            raise ChaosDecodeError("Cannot encode empty text")

        rounds = self._rng.randint(self._min, self._max)
        salt = _make_salt(text)

        scrambled = text
        # Apply rounds 1..R in order so the outermost layer is round R.
        # Decode peels from R down to 1, so rounds_remaining correctly
        # identifies which cipher to invert at each step.
        for r in range(1, rounds + 1):
            key = _cipher_key(r, salt)
            cipher = CIPHER_CHAIN[r % len(CIPHER_CHAIN)]()
            scrambled = cipher.encipher(scrambled, key)

        header = _pack_header(rounds, salt)
        encoded = header + scrambled

        from stratum.core.events import ChaosEncoded
        _emit(ChaosEncoded(text_length=len(text)))

        return encoded

    def decode(self, encoded: str) -> Tuple[str, bool]:
        """
        Peel one scramble layer.  Returns (result, is_final).
        When is_final is True, result starts with DECODED_PREFIX.
        """
        if len(encoded) <= _HEADER_SIZE:
            raise ChaosDecodeError(
                "Input is too short to be chaos-encoded. "
                "Make sure you're passing the complete encoded string."
            )

        header = encoded[:_HEADER_SIZE]
        content = encoded[_HEADER_SIZE:]
        rounds, salt = _unpack_header(header)

        if rounds == 0:
            raise ChaosDecodeError(
                "Round counter is already zero — already fully decoded."
            )

        key = _cipher_key(rounds, salt)
        cipher = CIPHER_CHAIN[rounds % len(CIPHER_CHAIN)]()
        deciphered = cipher.decipher(content, key)

        new_rounds = rounds - 1

        from stratum.core.events import ChaosDecodeAttempt, ChaosFinalDecoded

        if new_rounds == 0:
            seed = int.from_bytes(salt, "big")
            drifted = self._drift.drift(deciphered, seed)
            drift_count = self._drift.count_drifted(deciphered, drifted)
            _emit(ChaosDecodeAttempt(is_final=True))
            _emit(ChaosFinalDecoded(output_length=len(drifted), drift_count=drift_count))
            return DECODED_PREFIX + drifted, True
        else:
            new_header = _pack_header(new_rounds, salt)
            _emit(ChaosDecodeAttempt(is_final=False))
            return new_header + deciphered, False

    def is_encoded(self, text: str) -> bool:
        if len(text) <= _HEADER_SIZE:
            return False
        try:
            _unpack_header(text[:_HEADER_SIZE])
            return True
        except ChaosDecodeError:
            return False

    def rounds_remaining(self, encoded: str) -> int:
        """Return how many decode rounds remain. Intentionally not exposed to plugins."""
        if len(encoded) <= _HEADER_SIZE:
            return 0
        try:
            rounds, _ = _unpack_header(encoded[:_HEADER_SIZE])
            return rounds
        except ChaosDecodeError:
            return 0
