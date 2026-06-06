"""Tests for the Result monad."""

import pytest
from stratum.core.result import Ok, Err, collect_results, result_of, UnwrapError


def test_ok_is_ok():
    r = Ok(42)
    assert r.is_ok()
    assert not r.is_err()


def test_err_is_err():
    r = Err("fail")
    assert r.is_err()
    assert not r.is_ok()


def test_ok_unwrap():
    assert Ok("hello").unwrap() == "hello"


def test_err_unwrap_raises():
    with pytest.raises(UnwrapError):
        Err("nope").unwrap()


def test_ok_map():
    assert Ok(5).map(lambda x: x * 2) == Ok(10)


def test_err_map_does_not_transform():
    assert Err("e").map(lambda x: x * 2) == Err("e")


def test_flat_map_ok():
    result = Ok(5).flat_map(lambda x: Ok(x + 1))
    assert result == Ok(6)


def test_flat_map_ok_to_err():
    result = Ok(5).flat_map(lambda x: Err("bad"))
    assert result == Err("bad")


def test_flat_map_err_short_circuits():
    called = []
    result = Err("e").flat_map(lambda x: (called.append(x), Ok(x))[1])
    assert result == Err("e")
    assert called == []


def test_unwrap_or():
    assert Ok(1).unwrap_or(99) == 1
    assert Err("x").unwrap_or(99) == 99


def test_unwrap_or_else():
    assert Ok(1).unwrap_or_else(lambda e: 0) == 1
    assert Err("x").unwrap_or_else(lambda e: len(e)) == 1


def test_tap_ok():
    side = []
    Ok("hi").tap(side.append)
    assert side == ["hi"]


def test_tap_err_not_called_on_ok():
    side = []
    Ok("hi").tap_err(side.append)
    assert side == []


def test_tap_err():
    side = []
    Err("boom").tap_err(side.append)
    assert side == ["boom"]


def test_collect_all_ok():
    result = collect_results([Ok(1), Ok(2), Ok(3)])
    assert result == Ok([1, 2, 3])


def test_collect_short_circuits_on_first_err():
    result = collect_results([Ok(1), Err("x"), Ok(3)])
    assert result == Err("x")


def test_result_of_decorator_ok():
    @result_of
    def add(a, b):
        return a + b

    assert add(2, 3) == Ok(5)


def test_result_of_decorator_err():
    @result_of
    def boom():
        raise ValueError("no")

    r = boom()
    assert r.is_err()
    assert isinstance(r.unwrap_err(), ValueError)


def test_iter_ok():
    values = list(Ok(42))
    assert values == [42]


def test_iter_err():
    values = list(Err("x"))
    assert values == []


def test_ok_equality():
    assert Ok(1) == Ok(1)
    assert Ok(1) != Ok(2)
    assert Ok(1) != Err(1)


def test_err_equality():
    assert Err("a") == Err("a")
    assert Err("a") != Err("b")
