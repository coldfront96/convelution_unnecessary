"""Tests for the chaos encoding subsystem."""

import random

import pytest

from stratum.plugins.chaos.ciphers import (
    CIPHER_CHAIN,
    RotCipher,
    VigenereCipher,
    TranspositionCipher,
    SubstitutionCipher,
    InterleaveCipher,
    ScrambleCipher,
    PRINTABLE_LOW,
    PRINTABLE_HIGH,
)
from stratum.plugins.chaos.drift import PhoneticDrift, VisualDrift, CompoundDrift
from stratum.plugins.chaos.codec import (
    ChaosCodec,
    ChaosDecodeError,
    DECODED_PREFIX,
    _pack_header,
    _unpack_header,
)
from stratum.plugins.chaos.plugins import (
    ChaosEncodePlugin,
    ChaosDecodePlugin,
    ChaosPluginFactory,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_TEXTS = [
    "hello world",
    "the quick brown fox",
    "STRATUM is unnecessarily complex",
    "12345",
    "a",
    "  spaces  ",
]

def _full_decode(codec: ChaosCodec, encoded: str) -> tuple[str, int]:
    """Decode until final, return (result, rounds_taken)."""
    current = encoded
    rounds = 0
    while True:
        result, is_final = codec.decode(current)
        rounds += 1
        if is_final:
            return result, rounds
        current = result


def _make_codec(min_r=3, max_r=6, seed=42, drift=0.15):
    return ChaosCodec(min_rounds=min_r, max_rounds=max_r,
                      drift_rate=drift, rng=random.Random(seed))


# ---------------------------------------------------------------------------
# Cipher invertibility tests
# ---------------------------------------------------------------------------

CIPHER_CLASSES = [RotCipher, VigenereCipher, TranspositionCipher,
                  SubstitutionCipher, InterleaveCipher, ScrambleCipher]


@pytest.mark.parametrize("CipherClass", CIPHER_CLASSES)
@pytest.mark.parametrize("text", SAMPLE_TEXTS)
def test_cipher_round_trip(CipherClass, text):
    """encipher then decipher must return the original text."""
    cipher = CipherClass()
    for key in (0, 1, 42, 999, 0xDEAD):
        enciphered = cipher.encipher(text, key)
        assert cipher.decipher(enciphered, key) == text, (
            f"{CipherClass.__name__} round-trip failed for key={key!r}, text={text!r}"
        )


@pytest.mark.parametrize("CipherClass", CIPHER_CLASSES)
def test_cipher_preserves_length(CipherClass):
    """All ciphers must preserve the string length (they operate in-place on chars)."""
    cipher = CipherClass()
    for text in SAMPLE_TEXTS:
        enc = cipher.encipher(text, 7)
        assert len(enc) == len(text), f"{CipherClass.__name__} changed length"


@pytest.mark.parametrize("CipherClass", CIPHER_CLASSES)
def test_cipher_output_is_printable_ascii(CipherClass):
    """Output characters must all be in the printable ASCII range."""
    cipher = CipherClass()
    for text in SAMPLE_TEXTS:
        enc = cipher.encipher(text, 17)
        for ch in enc:
            assert PRINTABLE_LOW <= ord(ch) <= PRINTABLE_HIGH, (
                f"{CipherClass.__name__} produced non-printable char {ch!r}"
            )


@pytest.mark.parametrize("CipherClass", CIPHER_CLASSES)
def test_cipher_different_keys_different_output(CipherClass):
    """Different keys should (almost always) produce different ciphertexts."""
    cipher = CipherClass()
    text = "hello world"
    results = {cipher.encipher(text, k) for k in range(10)}
    assert len(results) > 1, f"{CipherClass.__name__} appears key-insensitive"


def test_cipher_chain_length():
    assert len(CIPHER_CHAIN) == 6


# ---------------------------------------------------------------------------
# Header pack / unpack tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rounds", [1, 5, 15, 50, 93, 94, 95, 200, 500])
def test_header_round_trip(rounds):
    import os
    salt_raw = os.urandom(4)
    # Salt bytes are reduced mod 94 before storage; compare against that
    expected_salt = bytes(b % 94 for b in salt_raw)
    header = _pack_header(rounds, salt_raw)
    assert len(header) == 8
    recovered_rounds, recovered_salt = _unpack_header(header)
    assert recovered_rounds == rounds
    assert recovered_salt == expected_salt


def test_header_looks_printable():
    header = _pack_header(42, b"\x12\x34\x56\x78")
    for ch in header:
        assert 33 <= ord(ch) <= 126, f"header has space/control char: {ch!r}"


def test_header_checksum_mismatch_raises():
    header = _pack_header(10, b"\x00\x01\x02\x03")
    corrupted = header[:-1] + chr(ord(header[-1]) ^ 0x1)
    with pytest.raises(ChaosDecodeError, match="Checksum"):
        _unpack_header(corrupted)


# ---------------------------------------------------------------------------
# ChaosCodec encode/decode tests
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text", SAMPLE_TEXTS)
def test_encode_decode_full_round_trip(text):
    """After all rounds, decoded text starts with DECODED_PREFIX."""
    codec = _make_codec()
    encoded = codec.encode(text)
    result, _ = _full_decode(codec, encoded)
    assert result.startswith(DECODED_PREFIX)


@pytest.mark.parametrize("text", SAMPLE_TEXTS)
def test_cipher_round_trip_no_drift(text):
    """With drift=0, the decoded text must be exactly the original."""
    codec = _make_codec(drift=0.0)
    encoded = codec.encode(text)
    result, _ = _full_decode(codec, encoded)
    decoded = result[len(DECODED_PREFIX):]
    assert decoded == text, f"Round-trip failed: original={text!r} decoded={decoded!r}"


def test_encode_output_looks_scrambled():
    codec = _make_codec()
    text = "hello world"
    encoded = codec.encode(text)
    content = encoded[8:]  # skip header
    assert content != text, "encoded output should not equal original"


def test_encode_same_text_different_rounds():
    """Two encodes of the same text may have different round counts (random)."""
    text = "test"
    codec1 = _make_codec(seed=1)
    codec2 = _make_codec(seed=99)
    enc1 = codec1.encode(text)
    enc2 = codec2.encode(text)
    # They might coincidentally be equal but usually won't be
    # At minimum verify both are valid
    assert codec1.is_encoded(enc1)
    assert codec2.is_encoded(enc2)


def test_encode_different_texts_different_output():
    codec = _make_codec(seed=7)
    enc1 = codec.encode("hello")
    enc2 = codec.encode("world")
    assert enc1 != enc2


def test_decode_not_chaos_encoded_raises():
    codec = _make_codec()
    with pytest.raises(ChaosDecodeError):
        codec.decode("this is not chaos encoded text")


def test_decode_too_short_raises():
    codec = _make_codec()
    with pytest.raises(ChaosDecodeError):
        codec.decode("short")


def test_decode_empty_raises():
    codec = _make_codec()
    with pytest.raises(ChaosDecodeError):
        codec.decode("")


def test_encode_empty_raises():
    codec = _make_codec()
    with pytest.raises(ChaosDecodeError):
        codec.encode("")


def test_is_encoded_true_for_encoded():
    codec = _make_codec()
    enc = codec.encode("test")
    assert codec.is_encoded(enc)


def test_is_encoded_false_for_plaintext():
    codec = _make_codec()
    assert not codec.is_encoded("hello world")


def test_is_encoded_false_for_short():
    codec = _make_codec()
    assert not codec.is_encoded("hi")


def test_rounds_remaining_decrements():
    codec = _make_codec(min_r=5, max_r=5)  # always exactly 5 rounds
    enc = codec.encode("hello")
    assert codec.rounds_remaining(enc) == 5
    result, _ = codec.decode(enc)
    assert codec.rounds_remaining(result) == 4


def test_single_decode_not_final_for_multiple_rounds():
    codec = _make_codec(min_r=5, max_r=5)
    enc = codec.encode("hello")
    _, is_final = codec.decode(enc)
    assert not is_final


def test_final_decode_returns_decoded_prefix():
    codec = _make_codec(min_r=1, max_r=1, drift=0.0)  # 1 round, no drift
    enc = codec.encode("hello")
    result, is_final = codec.decode(enc)
    assert is_final
    assert result.startswith(DECODED_PREFIX)
    assert "hello" in result  # no drift → exact match


def test_drift_is_deterministic():
    """Same text always produces the same drift output."""
    codec = _make_codec(min_r=1, max_r=1, seed=42)
    enc1 = codec.encode("hello world")
    r1, _ = codec.decode(enc1)

    codec2 = _make_codec(min_r=1, max_r=1, seed=42)
    enc2 = codec2.encode("hello world")
    r2, _ = codec2.decode(enc2)

    assert r1 == r2


def test_no_countdown_in_output():
    """The encoded/decoded output must not contain round count as visible text."""
    codec = _make_codec(min_r=3, max_r=7)
    enc = codec.encode("hello world")
    current = enc
    while True:
        for marker in ["round", "Round", "ROUND", "remaining", "left", "/"]:
            assert marker not in current, f"found hint {marker!r} in output"
        result, is_final = codec.decode(current)
        if is_final:
            break
        current = result


def test_full_cycle_with_fixed_rounds():
    """Ensure the codec correctly completes N rounds with known N."""
    for n in [1, 2, 5, 10]:
        codec = ChaosCodec(min_rounds=n, max_rounds=n, drift_rate=0.0,
                           rng=random.Random(n))
        enc = codec.encode("test text")
        result, rounds_taken = _full_decode(codec, enc)
        assert rounds_taken == n
        assert result.startswith(DECODED_PREFIX)


# ---------------------------------------------------------------------------
# Drift strategy tests
# ---------------------------------------------------------------------------

def test_phonetic_drift_changes_some_chars():
    drift = PhoneticDrift(drift_rate=1.0)  # force all eligible chars to drift
    result = drift.drift("hello world", seed=42)
    assert result != "hello world"


def test_phonetic_drift_zero_rate_no_change():
    drift = PhoneticDrift(drift_rate=0.0)
    result = drift.drift("hello world", seed=42)
    assert result == "hello world"


def test_visual_drift_changes_some_chars():
    drift = VisualDrift(drift_rate=1.0)
    result = drift.drift("hello", seed=0)
    assert result != "hello"


def test_visual_drift_zero_rate_no_change():
    drift = VisualDrift(drift_rate=0.0)
    assert drift.drift("hello", seed=0) == "hello"


def test_compound_drift_deterministic():
    drift = CompoundDrift(drift_rate=0.5)
    assert drift.drift("hello world", 99) == drift.drift("hello world", 99)


def test_compound_drift_different_seeds_may_differ():
    drift = CompoundDrift(drift_rate=0.5)
    results = {drift.drift("hello world", seed) for seed in range(20)}
    assert len(results) > 1


# ---------------------------------------------------------------------------
# Plugin tests
# ---------------------------------------------------------------------------

def _make_plugins(min_r=3, max_r=6, seed=42):
    codec = ChaosCodec(min_rounds=min_r, max_rounds=max_r,
                       drift_rate=0.1, rng=random.Random(seed))
    return ChaosEncodePlugin(codec), ChaosDecodePlugin(codec), codec


def test_encode_plugin_returns_ok():
    enc_p, _, _ = _make_plugins()
    result = enc_p.execute("hello world")
    assert result.is_ok()


def test_encode_plugin_output_is_chaos_encoded():
    enc_p, _, codec = _make_plugins()
    output = enc_p.execute("hello world").unwrap()
    assert codec.is_encoded(output)


def test_encode_plugin_empty_input_returns_err():
    enc_p, _, _ = _make_plugins()
    result = enc_p.execute("")
    assert result.is_err()


def test_decode_plugin_returns_ok():
    enc_p, dec_p, _ = _make_plugins()
    encoded = enc_p.execute("hi").unwrap()
    result = dec_p.execute(encoded)
    assert result.is_ok()


def test_decode_plugin_on_plaintext_returns_err():
    _, dec_p, _ = _make_plugins()
    result = dec_p.execute("this is just plain text with no header")
    assert result.is_err()


def test_decode_plugin_eventually_produces_decoded_prefix():
    enc_p, dec_p, _ = _make_plugins(min_r=2, max_r=4)
    current = enc_p.execute("hello world").unwrap()
    for _ in range(20):
        result = dec_p.execute(current)
        assert result.is_ok(), f"decode failed: {result.unwrap_err()}"
        val = result.unwrap()
        if val.startswith(DECODED_PREFIX):
            break
        current = val
    else:
        pytest.fail("Never reached DECODED_PREFIX after 20 rounds")


def test_plugin_category_is_chaos():
    enc_p, dec_p, _ = _make_plugins()
    assert enc_p.category == "chaos"
    assert dec_p.category == "chaos"


def test_factory_creates_both_plugins():
    factory = ChaosPluginFactory(min_rounds=5, max_rounds=10)
    plugins = factory.create_plugins()
    names = {p.name for p in plugins}
    assert "chaos_encode" in names
    assert "chaos_decode" in names


# ---------------------------------------------------------------------------
# Integration: chaos via PluginRegistry
# ---------------------------------------------------------------------------

def test_chaos_via_registry():
    from stratum.core.events import EventBus, EventStore
    from stratum.plugins.registry import PluginRegistry

    bus = EventBus()
    store = EventStore(db_path=":memory:")
    registry = PluginRegistry(event_bus=bus, event_store=store)
    factory = ChaosPluginFactory(min_rounds=2, max_rounds=4)
    registry.register_from_factory(factory)

    assert "chaos_encode" in registry
    assert "chaos_decode" in registry

    enc = registry.invoke("chaos_encode", "hello world")
    assert enc.is_ok()
    encoded = enc.unwrap()

    current = encoded
    for _ in range(20):
        dec = registry.invoke("chaos_decode", current)
        assert dec.is_ok()
        val = dec.unwrap()
        if val.startswith(DECODED_PREFIX):
            decoded = val[len(DECODED_PREFIX):]
            # Should be close to "hello world"
            assert len(decoded) >= len("hello world") - 2
            break
        current = val
    else:
        pytest.fail("Never reached DECODED_PREFIX via registry")
