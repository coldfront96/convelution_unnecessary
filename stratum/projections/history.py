"""
History projection. Builds a queryable list of all transformations
by subscribing to TransformationStarted and TransformationCompleted events.

This is not a database query — it's a read-model derived from the event log.
Current state is always a projection; nothing is stored independently.

Pattern: Observer (subscribes to bus), Iterator (TransformationRecord list).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import List, Optional

from stratum.core.events import (
    BaseEvent,
    EventBus,
    TransformationCompleted,
    TransformationFailed,
    TransformationStarted,
)


@dataclass
class TransformationRecord:
    session_id: str
    input_text: str
    program_source: str
    output_text: Optional[str] = None
    failed: bool = False
    error_message: Optional[str] = None
    duration_ms: float = 0.0
    instruction_count: int = 0

    @property
    def succeeded(self) -> bool:
        return not self.failed and self.output_text is not None


class HistoryProjection:
    """
    Maintains an ordered list of TransformationRecords.
    Subscribes to the EventBus and updates itself on each relevant event.
    Can also be rebuilt by replaying from the EventStore.
    """

    def __init__(self, event_bus: EventBus, max_records: int = 1000) -> None:
        self._records: List[TransformationRecord] = []
        self._pending: dict[str, TransformationRecord] = {}
        self._lock = threading.Lock()
        self._max_records = max_records

        event_bus.subscribe(TransformationStarted, self._on_started)
        event_bus.subscribe(TransformationCompleted, self._on_completed)
        event_bus.subscribe(TransformationFailed, self._on_failed)

    def _on_started(self, event: TransformationStarted) -> None:
        record = TransformationRecord(
            session_id=event.session_id,
            input_text=event.input_text,
            program_source=event.program_source,
        )
        with self._lock:
            self._pending[event.session_id] = record

    def _on_completed(self, event: TransformationCompleted) -> None:
        with self._lock:
            record = self._pending.pop(event.session_id, None)
            if record is None:
                record = TransformationRecord(
                    session_id=event.session_id,
                    input_text=event.input_text,
                    program_source="",
                )
            record.output_text = event.output_text
            record.duration_ms = event.duration_ms
            record.instruction_count = event.instruction_count
            self._records.append(record)
            if len(self._records) > self._max_records:
                self._records.pop(0)

    def _on_failed(self, event: TransformationFailed) -> None:
        with self._lock:
            record = self._pending.pop(event.session_id, None)
            if record is None:
                record = TransformationRecord(
                    session_id=event.session_id,
                    input_text="",
                    program_source="",
                )
            record.failed = True
            record.error_message = event.error_message
            self._records.append(record)

    def all(self) -> List[TransformationRecord]:
        with self._lock:
            return list(self._records)

    def last(self, n: int = 10) -> List[TransformationRecord]:
        with self._lock:
            return list(self._records[-n:])

    def successful(self) -> List[TransformationRecord]:
        with self._lock:
            return [r for r in self._records if r.succeeded]

    def failed(self) -> List[TransformationRecord]:
        with self._lock:
            return [r for r in self._records if r.failed]

    def search(self, query: str) -> List[TransformationRecord]:
        with self._lock:
            return [
                r for r in self._records
                if query in r.input_text or query in (r.output_text or "") or query in r.program_source
            ]

    def count(self) -> int:
        with self._lock:
            return len(self._records)

    def rebuild_from_events(self, events: List[BaseEvent]) -> None:
        """Replay a list of events to rebuild projection from scratch."""
        with self._lock:
            self._records.clear()
            self._pending.clear()
        for event in events:
            if isinstance(event, TransformationStarted):
                self._on_started(event)
            elif isinstance(event, TransformationCompleted):
                self._on_completed(event)
            elif isinstance(event, TransformationFailed):
                self._on_failed(event)
