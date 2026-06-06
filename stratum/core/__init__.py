"""Core infrastructure: Result monad, event bus, DI container, config."""

from stratum.core.result import Ok, Err, Result, collect_results, result_of
from stratum.core.events import BaseEvent, EventBus, EventStore
from stratum.core.container import Container, Scope
from stratum.core.config import Config, load_config

__all__ = [
    "Ok", "Err", "Result", "collect_results", "result_of",
    "BaseEvent", "EventBus", "EventStore",
    "Container", "Scope",
    "Config", "load_config",
]
