"""
Entry point: python -m stratum

Boots the system via the DI container, then hands off to the CLI.
All components are registered with the container and resolved from it —
nothing is constructed by hand outside of this file.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

from stratum.core.config import load_config
from stratum.core.container import Container, Scope
from stratum.core.events import EventBus, EventStore
from stratum.orchestrator import Orchestrator
from stratum.plugins.base import BuiltinPluginFactory
from stratum.plugins.registry import PluginRegistry
from stratum.projections.history import HistoryProjection
from stratum.projections.stats import StatsProjection
from stratum.api.cli import run_cli
from stratum.api.http_server import create_http_server
from stratum.query.executor import QueryExecutor
from stratum.plugins.watcher import PluginDirectoryWatcher


def _find_config() -> Path | None:
    candidates = [
        Path("config/stratum.toml"),
        Path("stratum.toml"),
        Path.home() / ".config" / "stratum" / "stratum.toml",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def boot() -> tuple[Container, Orchestrator, PluginRegistry, HistoryProjection, StatsProjection, Any, EventBus]:
    """
    Bootstrap the entire system. Returns the container and key singletons.
    All wiring happens here; nothing else constructs its own dependencies.
    """
    config_path = _find_config()
    config = load_config(config_path)

    # Configure logging first
    logging.basicConfig(
        level=getattr(logging, config.logging.level),
        format=config.logging.format,
    )

    container = Container()

    # Wire core infrastructure
    event_bus = EventBus()
    event_store = EventStore(db_path=config.effective_db_path())

    container.register_instance(EventBus, event_bus)
    container.register_instance(EventStore, event_store)

    # Wire plugin registry and load builtins
    registry = PluginRegistry(event_bus=event_bus, event_store=event_store)
    if config.plugins.load_builtins:
        registry.register_from_factory(BuiltinPluginFactory())
    container.register_instance(PluginRegistry, registry)

    # Wire projections (they self-subscribe to the bus in __init__)
    history = HistoryProjection(event_bus=event_bus)
    stats = StatsProjection(event_bus=event_bus)
    container.register_instance(HistoryProjection, history)
    container.register_instance(StatsProjection, stats)

    # Wire orchestrator
    orchestrator = Orchestrator(
        registry=registry,
        event_bus=event_bus,
        event_store=event_store,
        config=config,
    )
    container.register_instance(Orchestrator, orchestrator)

    # Replay history from event store to rebuild projections
    all_events = event_store.get_all()
    if all_events:
        history.rebuild_from_events(all_events)

    return container, orchestrator, registry, history, stats, config, event_bus


def main() -> int:
    container, orchestrator, registry, history, stats, config, event_bus = boot()

    query_executor = QueryExecutor(history)

    # Start hot-reload watcher if extra_plugin_dirs are configured
    watcher: PluginDirectoryWatcher | None = None
    if config.plugins.extra_plugin_dirs:
        from pathlib import Path
        watcher = PluginDirectoryWatcher(
            directories=[Path(d) for d in config.plugins.extra_plugin_dirs],
            registry=registry,
            event_bus=event_bus,
        )
        watcher.start()

    def http_server_factory():
        return create_http_server(
            orchestrator=orchestrator,
            registry=registry,
            history_projection=history,
            stats_projection=stats,
            host=config.api.http_host,
            port=config.api.http_port,
            query_executor=query_executor,
        )

    return run_cli(
        args=sys.argv[1:],
        orchestrator=orchestrator,
        registry=registry,
        history_projection=history,
        stats_projection=stats,
        http_server_factory=http_server_factory,
        config=config,
        query_executor=query_executor,
    )


if __name__ == "__main__":
    sys.exit(main())
