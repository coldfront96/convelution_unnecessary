"""
Result monad. Forces explicit handling of Ok/Err paths.
No exceptions cross subsystem boundaries — everything returns a Result.
"""

from __future__ import annotations

import functools
from abc import ABC, abstractmethod
from typing import Callable, Generic, Iterator, Optional, TypeVar

T = TypeVar("T")
E = TypeVar("E")
U = TypeVar("U")
F = TypeVar("F")


class ResultError(Exception):
    pass


class UnwrapError(ResultError):
    def __init__(self, message: str, error: object):
        super().__init__(message)
        self.error = error


class Result(ABC, Generic[T, E]):
    """
    Algebraic sum type. A Result is either Ok(T) or Err(E).
    Callers must handle both cases; there is no implicit unwrapping.

    Pattern: supports both explicit branching (is_ok/is_err) and
    functional chaining (map/flat_map/and_then).
    """

    @abstractmethod
    def is_ok(self) -> bool: ...

    @abstractmethod
    def is_err(self) -> bool: ...

    @abstractmethod
    def map(self, f: Callable[[T], U]) -> Result[U, E]: ...

    @abstractmethod
    def map_err(self, f: Callable[[E], F]) -> Result[T, F]: ...

    @abstractmethod
    def flat_map(self, f: Callable[[T], Result[U, E]]) -> Result[U, E]: ...

    @abstractmethod
    def unwrap(self) -> T: ...

    @abstractmethod
    def unwrap_or(self, default: T) -> T: ...

    @abstractmethod
    def unwrap_or_else(self, f: Callable[[E], T]) -> T: ...

    @abstractmethod
    def expect(self, msg: str) -> T: ...

    @abstractmethod
    def ok(self) -> Optional[T]: ...

    @abstractmethod
    def err(self) -> Optional[E]: ...

    @abstractmethod
    def __iter__(self) -> Iterator[T]: ...

    # ---- derived combinators ------------------------------------------------

    def and_then(self, f: Callable[[T], Result[U, E]]) -> Result[U, E]:
        return self.flat_map(f)

    def or_else(self, f: Callable[[E], Result[T, F]]) -> Result[T, F]:
        if self.is_ok():
            return self  # type: ignore[return-value]
        assert isinstance(self, Err)
        return f(self._error)

    def unwrap_err(self) -> E:
        if self.is_err():
            assert isinstance(self, Err)
            return self._error
        assert isinstance(self, Ok)
        raise UnwrapError("called unwrap_err on Ok value", self._value)

    def tap(self, f: Callable[[T], None]) -> Result[T, E]:
        """Run a side effect on Ok value without transforming it."""
        if self.is_ok():
            assert isinstance(self, Ok)
            f(self._value)
        return self

    def tap_err(self, f: Callable[[E], None]) -> Result[T, E]:
        """Run a side effect on Err value without transforming it."""
        if self.is_err():
            assert isinstance(self, Err)
            f(self._error)
        return self


class Ok(Result[T, E]):
    """The success variant of Result."""

    __slots__ = ("_value",)

    def __init__(self, value: T):
        self._value = value

    def is_ok(self) -> bool:
        return True

    def is_err(self) -> bool:
        return False

    def map(self, f: Callable[[T], U]) -> Result[U, E]:
        return Ok(f(self._value))

    def map_err(self, f: Callable[[E], F]) -> Result[T, F]:
        return Ok(self._value)  # type: ignore[return-value]

    def flat_map(self, f: Callable[[T], Result[U, E]]) -> Result[U, E]:
        return f(self._value)

    def unwrap(self) -> T:
        return self._value

    def unwrap_or(self, default: T) -> T:
        return self._value

    def unwrap_or_else(self, f: Callable[[E], T]) -> T:
        return self._value

    def expect(self, msg: str) -> T:
        return self._value

    def ok(self) -> Optional[T]:
        return self._value

    def err(self) -> Optional[E]:
        return None

    def __iter__(self) -> Iterator[T]:
        yield self._value

    def __repr__(self) -> str:
        return f"Ok({self._value!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Ok) and self._value == other._value

    def __hash__(self) -> int:
        try:
            return hash(("Ok", self._value))
        except TypeError:
            return hash(("Ok", id(self._value)))


class Err(Result[T, E]):
    """The failure variant of Result."""

    __slots__ = ("_error",)

    def __init__(self, error: E):
        self._error = error

    def is_ok(self) -> bool:
        return False

    def is_err(self) -> bool:
        return True

    def map(self, f: Callable[[T], U]) -> Result[U, E]:
        return Err(self._error)  # type: ignore[return-value]

    def map_err(self, f: Callable[[E], F]) -> Result[T, F]:
        return Err(f(self._error))

    def flat_map(self, f: Callable[[T], Result[U, E]]) -> Result[U, E]:
        return Err(self._error)  # type: ignore[return-value]

    def unwrap(self) -> T:
        raise UnwrapError(
            f"called unwrap on Err value: {self._error!r}", self._error
        )

    def unwrap_or(self, default: T) -> T:
        return default

    def unwrap_or_else(self, f: Callable[[E], T]) -> T:
        return f(self._error)

    def expect(self, msg: str) -> T:
        raise UnwrapError(f"{msg}: {self._error!r}", self._error)

    def ok(self) -> Optional[T]:
        return None

    def err(self) -> Optional[E]:
        return self._error

    def __iter__(self) -> Iterator[T]:
        return iter([])

    def __repr__(self) -> str:
        return f"Err({self._error!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Err) and self._error == other._error

    def __hash__(self) -> int:
        try:
            return hash(("Err", self._error))
        except TypeError:
            return hash(("Err", id(self._error)))


# ---- free functions ---------------------------------------------------------


def collect_results(results: list[Result[T, E]]) -> Result[list[T], E]:
    """Turn list[Result[T, E]] into Result[list[T], E]. First Err short-circuits."""
    values: list[T] = []
    for r in results:
        if r.is_err():
            return r  # type: ignore[return-value]
        values.append(r.unwrap())
    return Ok(values)


def result_of(f: Callable[..., T]) -> Callable[..., Result[T, Exception]]:
    """Decorator: wraps return value in Ok, exceptions in Err."""

    @functools.wraps(f)
    def wrapper(*args: object, **kwargs: object) -> Result[T, Exception]:
        try:
            return Ok(f(*args, **kwargs))
        except Exception as exc:
            return Err(exc)

    return wrapper
