"""
Stats projection. Tracks aggregate statistics over all plugin calls
and transformations by subscribing to the event bus.

Pattern: Observer (subscribes to bus).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Dict, List

from stratum.core.events import (
    EventBus,
    PluginCalled,
    TransformationCompleted,
    TransformationFailed,
)


@dataclass
class PluginStats:
    plugin_name: str
    call_count: int = 0
    total_duration_us: int = 0
    failure_count: int = 0
    total_input_chars: int = 0
    total_output_chars: int = 0

    @property
    def avg_duration_us(self) -> float:
        if self.call_count == 0:
            return 0.0
        return self.total_duration_us / self.call_count

    @property
    def success_rate(self) -> float:
        if self.call_count == 0:
            return 1.0
        return (self.call_count - self.failure_count) / self.call_count


@dataclass
class GlobalStats:
    total_transformations: int = 0
    successful_transformations: int = 0
    failed_transformations: int = 0
    total_duration_ms: float = 0.0
    total_instructions_executed: int = 0

    @property
    def avg_duration_ms(self) -> float:
        if self.total_transformations == 0:
            return 0.0
        return self.total_duration_ms / self.total_transformations

    @property
    def success_rate(self) -> float:
        if self.total_transformations == 0:
            return 1.0
        return self.successful_transformations / self.total_transformations


class StatsProjection:
    """Aggregate statistics over all plugin calls and transformations."""

    def __init__(self, event_bus: EventBus) -> None:
        self._plugin_stats: Dict[str, PluginStats] = {}
        self._global = GlobalStats()
        self._lock = threading.Lock()

        event_bus.subscribe(PluginCalled, self._on_plugin_called)
        event_bus.subscribe(TransformationCompleted, self._on_completed)
        event_bus.subscribe(TransformationFailed, self._on_failed)

    def _on_plugin_called(self, event: PluginCalled) -> None:
        with self._lock:
            if event.plugin_name not in self._plugin_stats:
                self._plugin_stats[event.plugin_name] = PluginStats(event.plugin_name)
            ps = self._plugin_stats[event.plugin_name]
            ps.call_count += 1
            ps.total_duration_us += event.duration_us
            ps.total_input_chars += event.input_length
            ps.total_output_chars += event.output_length
            if not event.succeeded:
                ps.failure_count += 1

    def _on_completed(self, event: TransformationCompleted) -> None:
        with self._lock:
            self._global.total_transformations += 1
            self._global.successful_transformations += 1
            self._global.total_duration_ms += event.duration_ms
            self._global.total_instructions_executed += event.instruction_count

    def _on_failed(self, event: TransformationFailed) -> None:
        with self._lock:
            self._global.total_transformations += 1
            self._global.failed_transformations += 1

    def plugin_stats(self, name: str) -> PluginStats | None:
        with self._lock:
            return self._plugin_stats.get(name)

    def all_plugin_stats(self) -> List[PluginStats]:
        with self._lock:
            return sorted(self._plugin_stats.values(), key=lambda s: s.call_count, reverse=True)

    def global_stats(self) -> GlobalStats:
        with self._lock:
            import copy
            return copy.copy(self._global)

    def top_plugins(self, n: int = 5) -> List[PluginStats]:
        return self.all_plugin_stats()[:n]

    def reset(self) -> None:
        with self._lock:
            self._plugin_stats.clear()
            self._global = GlobalStats()
