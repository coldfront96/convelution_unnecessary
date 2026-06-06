"""
Event bus, event store, and base event types.

All cross-component communication flows through the EventBus.
State is never stored directly — only as a projection of the EventStore.

Patterns: Observer (subscribers), Chain of Responsibility (middleware),
          Command (events as command records).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Type, TypeVar

logger = logging.getLogger(__name__)

E = TypeVar("E", bound="BaseEvent")


# ---- base event -------------------------------------------------------------


@dataclass
class BaseEvent:
    """
    Every event in the system inherits from this.
    Fields are set automatically; subclasses only add domain data.
    """

    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    sequence: int = field(default=0)  # assigned by EventStore on append

    @property
    def event_type(self) -> str:
        return type(self).__name__

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["event_type"] = self.event_type
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BaseEvent":
        data = {k: v for k, v in data.items() if k != "event_type"}
        return cls(**data)


# ---- domain events ----------------------------------------------------------


@dataclass
class TransformationStarted(BaseEvent):
    input_text: str = ""
    program_source: str = ""
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))


@dataclass
class TokensProduced(BaseEvent):
    session_id: str = ""
    token_count: int = 0
    token_summary: str = ""


@dataclass
class AstProduced(BaseEvent):
    session_id: str = ""
    node_count: int = 0
    root_type: str = ""


@dataclass
class OptimizationApplied(BaseEvent):
    session_id: str = ""
    pass_name: str = ""
    nodes_removed: int = 0
    nodes_rewritten: int = 0


@dataclass
class BytecodeProduced(BaseEvent):
    session_id: str = ""
    instruction_count: int = 0
    register_count: int = 0


@dataclass
class InstructionExecuted(BaseEvent):
    session_id: str = ""
    opcode: str = ""
    ip: int = 0
    register_snapshot: str = ""  # JSON


@dataclass
class PluginCalled(BaseEvent):
    session_id: str = ""
    plugin_name: str = ""
    input_length: int = 0
    output_length: int = 0
    duration_us: int = 0
    succeeded: bool = True


@dataclass
class TransformationCompleted(BaseEvent):
    session_id: str = ""
    input_text: str = ""
    output_text: str = ""
    duration_ms: float = 0.0
    instruction_count: int = 0


@dataclass
class TransformationFailed(BaseEvent):
    session_id: str = ""
    phase: str = ""
    error_message: str = ""


@dataclass
class TypeCheckCompleted(BaseEvent):
    session_id: str = ""
    inferred_type: str = ""
    error_count: int = 0
    warning_count: int = 0


@dataclass
class PluginRegistered(BaseEvent):
    plugin_name: str = ""
    plugin_description: str = ""


# ---- event registry (maps type name -> class for deserialization) -----------


_EVENT_REGISTRY: dict[str, type[BaseEvent]] = {}


def register_event_type(cls: type[BaseEvent]) -> type[BaseEvent]:
    """Decorator that registers an event class for deserialization."""
    _EVENT_REGISTRY[cls.__name__] = cls
    return cls


def _register_builtins() -> None:
    for cls in [
        TransformationStarted, TokensProduced, AstProduced, OptimizationApplied,
        BytecodeProduced, InstructionExecuted, PluginCalled,
        TransformationCompleted, TransformationFailed, TypeCheckCompleted, PluginRegistered,
    ]:
        _EVENT_REGISTRY[cls.__name__] = cls


_register_builtins()


def deserialize_event(data: dict[str, Any]) -> BaseEvent:
    event_type = data.get("event_type", "")
    cls = _EVENT_REGISTRY.get(event_type, BaseEvent)
    return cls.from_dict(data)


# ---- middleware -------------------------------------------------------------


class EventMiddleware(ABC):
    """
    Chain of Responsibility pattern.
    Each middleware decides whether to forward the event to the next handler.
    """

    @abstractmethod
    def process(
        self, event: BaseEvent, next_handler: Callable[[BaseEvent], None]
    ) -> None: ...


class LoggingMiddleware(EventMiddleware):
    def process(
        self, event: BaseEvent, next_handler: Callable[[BaseEvent], None]
    ) -> None:
        logger.debug(
            "dispatching %s [id=%s seq=%d]",
            event.event_type,
            event.event_id,
            event.sequence,
        )
        next_handler(event)


class ValidationMiddleware(EventMiddleware):
    def process(
        self, event: BaseEvent, next_handler: Callable[[BaseEvent], None]
    ) -> None:
        if not event.event_id:
            raise ValueError(f"Event missing event_id: {event!r}")
        if not event.timestamp:
            raise ValueError(f"Event missing timestamp: {event!r}")
        next_handler(event)


class DeadLetterMiddleware(EventMiddleware):
    """Captures events that have no subscribers into a dead-letter list."""

    def __init__(self) -> None:
        self._dead_letters: list[BaseEvent] = []
        self._bus_ref: Optional[EventBus] = None

    def attach(self, bus: "EventBus") -> None:
        self._bus_ref = bus

    def process(
        self, event: BaseEvent, next_handler: Callable[[BaseEvent], None]
    ) -> None:
        next_handler(event)
        if self._bus_ref and not self._bus_ref.has_subscribers(event.event_type):
            self._dead_letters.append(event)
            logger.warning("Dead letter: %s [id=%s]", event.event_type, event.event_id)

    @property
    def dead_letters(self) -> list[BaseEvent]:
        return list(self._dead_letters)


# ---- subscription -----------------------------------------------------------


@dataclass(order=True)
class _Subscription:
    priority: int  # lower number = higher priority (used for sorting)
    handler: Callable[[BaseEvent], None] = field(compare=False)
    subscription_id: str = field(
        default_factory=lambda: str(uuid.uuid4()), compare=False
    )
    event_type_filter: Optional[str] = field(default=None, compare=False)


# ---- event bus --------------------------------------------------------------


class EventBus:
    """
    Central pub/sub dispatcher. All inter-component communication goes here.

    Pattern: Observer — subscribers register and are notified on publish.
    Pattern: Chain of Responsibility — middleware wraps each dispatch.
    """

    def __init__(self, middleware: Optional[list[EventMiddleware]] = None) -> None:
        self._type_handlers: Dict[str, list[_Subscription]] = {}
        self._catch_all: list[_Subscription] = []
        self._middleware: list[EventMiddleware] = middleware or [
            ValidationMiddleware(),
            LoggingMiddleware(),
        ]
        self._lock = threading.RLock()

    def subscribe(
        self,
        event_type: Type[E] | str,
        handler: Callable[[E], None],
        priority: int = 0,
    ) -> str:
        """Subscribe to a specific event type. Returns subscription_id."""
        type_name = (
            event_type if isinstance(event_type, str) else event_type.__name__
        )
        sub = _Subscription(
            priority=priority,
            handler=handler,  # type: ignore[arg-type]
            event_type_filter=type_name,
        )
        with self._lock:
            bucket = self._type_handlers.setdefault(type_name, [])
            bucket.append(sub)
            bucket.sort()
        return sub.subscription_id

    def subscribe_all(
        self, handler: Callable[[BaseEvent], None], priority: int = 0
    ) -> str:
        """Subscribe to all events regardless of type."""
        sub = _Subscription(priority=priority, handler=handler)
        with self._lock:
            self._catch_all.append(sub)
            self._catch_all.sort()
        return sub.subscription_id

    def unsubscribe(self, subscription_id: str) -> bool:
        with self._lock:
            for bucket in self._type_handlers.values():
                for sub in bucket:
                    if sub.subscription_id == subscription_id:
                        bucket.remove(sub)
                        return True
            for sub in self._catch_all:
                if sub.subscription_id == subscription_id:
                    self._catch_all.remove(sub)
                    return True
        return False

    def has_subscribers(self, event_type: str) -> bool:
        with self._lock:
            return bool(self._type_handlers.get(event_type)) or bool(self._catch_all)

    def publish(self, event: BaseEvent) -> None:
        """Dispatch event through middleware chain, then to all subscribers."""
        middleware = list(self._middleware)

        def dispatch(evt: BaseEvent) -> None:
            with self._lock:
                typed_subs = list(self._type_handlers.get(evt.event_type, []))
                catch_all_subs = list(self._catch_all)
            for sub in typed_subs + catch_all_subs:
                try:
                    sub.handler(evt)
                except Exception:
                    logger.exception(
                        "subscriber raised during %s dispatch", evt.event_type
                    )

        def run_chain(evt: BaseEvent, remaining: list[EventMiddleware]) -> None:
            if not remaining:
                dispatch(evt)
                return
            head, *tail = remaining
            head.process(evt, lambda e: run_chain(e, tail))

        run_chain(event, middleware)


# ---- event store ------------------------------------------------------------


class EventStore:
    """
    Append-only event log backed by SQLite.
    Current state is always derived by replaying events, never stored directly.

    Pattern: Singleton (one store per process, enforced by Container).
    """

    _instance: Optional["EventStore"] = None
    _init_lock = threading.Lock()

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self._write_lock = threading.Lock()
        self._sequence_counter = 0
        self._initialize()

    def _initialize(self) -> None:
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                sequence    INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id    TEXT NOT NULL UNIQUE,
                event_type  TEXT NOT NULL,
                timestamp   TEXT NOT NULL,
                payload     TEXT NOT NULL
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_event_type ON events(event_type)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_timestamp ON events(timestamp)"
        )
        self._conn.commit()
        row = self._conn.execute("SELECT MAX(sequence) FROM events").fetchone()
        self._sequence_counter = row[0] or 0

    def append(self, event: BaseEvent) -> BaseEvent:
        """Append event to store. Mutates event.sequence in place and returns it."""
        assert self._conn is not None
        payload = event.to_json()
        with self._write_lock:
            self._conn.execute(
                "INSERT INTO events (event_id, event_type, timestamp, payload) "
                "VALUES (?, ?, ?, ?)",
                (event.event_id, event.event_type, event.timestamp, payload),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT sequence FROM events WHERE event_id = ?", (event.event_id,)
            ).fetchone()
            event.sequence = row[0]
            self._sequence_counter = event.sequence
        return event

    def get_all(self) -> list[BaseEvent]:
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT payload FROM events ORDER BY sequence ASC"
        ).fetchall()
        return [deserialize_event(json.loads(r[0])) for r in rows]

    def get_by_type(self, event_type: str) -> list[BaseEvent]:
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT payload FROM events WHERE event_type = ? ORDER BY sequence ASC",
            (event_type,),
        ).fetchall()
        return [deserialize_event(json.loads(r[0])) for r in rows]

    def get_since(self, sequence: int) -> list[BaseEvent]:
        assert self._conn is not None
        rows = self._conn.execute(
            "SELECT payload FROM events WHERE sequence > ? ORDER BY sequence ASC",
            (sequence,),
        ).fetchall()
        return [deserialize_event(json.loads(r[0])) for r in rows]

    def replay(self, handler: Callable[[BaseEvent], None], since: int = 0) -> int:
        """Replay all events (optionally since a sequence number) through handler."""
        events = self.get_since(since)
        for evt in events:
            handler(evt)
        return len(events)

    @property
    def latest_sequence(self) -> int:
        return self._sequence_counter

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None
