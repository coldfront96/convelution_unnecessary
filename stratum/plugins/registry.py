"""
Plugin registry. The single source of truth for all available transforms.

The VM calls registry.invoke(name, input, *args, **kwargs) and the
registry routes to the correct plugin. It does not know or care how
the plugin is implemented.

Pattern: Prototype (plugins can be cloned from the registry),
         Flyweight (one plugin instance per name, shared across calls).
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Iterator, List, Optional

from stratum.core.events import EventBus, EventStore, PluginRegistered
from stratum.core.result import Err, Ok, Result
from stratum.plugins.base import PluginFactory, TransformPlugin

logger = logging.getLogger(__name__)


class PluginNotFoundError(Exception):
    pass


class PluginRegistry:
    """
    Manages all registered TransformPlugin instances.

    Plugins are indexed by name. Duplicate names raise on registration
    unless force=True.
    """

    def __init__(
        self,
        event_bus: EventBus,
        event_store: EventStore,
    ) -> None:
        self._plugins: Dict[str, TransformPlugin] = {}
        self._bus = event_bus
        self._store = event_store

    def register(self, plugin: TransformPlugin, force: bool = False) -> None:
        if plugin.name in self._plugins and not force:
            raise ValueError(
                f"Plugin {plugin.name!r} already registered. Use force=True to override."
            )
        self._plugins[plugin.name] = plugin
        event = PluginRegistered(
            plugin_name=plugin.name,
            plugin_description=plugin.description,
        )
        self._store.append(event)
        self._bus.publish(event)
        logger.debug("registered plugin: %s (%s)", plugin.name, plugin.category)

    def register_many(self, plugins: List[TransformPlugin], force: bool = False) -> None:
        for plugin in plugins:
            self.register(plugin, force=force)

    def register_from_factory(self, factory: PluginFactory, force: bool = False) -> None:
        self.register_many(factory.create_plugins(), force=force)

    def get(self, name: str) -> Optional[TransformPlugin]:
        return self._plugins.get(name)

    def require(self, name: str) -> TransformPlugin:
        plugin = self._plugins.get(name)
        if plugin is None:
            available = sorted(self._plugins.keys())
            raise PluginNotFoundError(
                f"No plugin named {name!r}. "
                f"Available: {available}"
            )
        return plugin

    def invoke(
        self, name: str, input_text: str, *args: Any, **kwargs: Any
    ) -> Result[str, str]:
        """
        Invoke a plugin by name. Positional args beyond input_text are
        passed through; kwargs are passed as named parameters.
        """
        try:
            plugin = self.require(name)
        except PluginNotFoundError as exc:
            return Err(str(exc))

        # Merge positional args into kwargs if possible
        all_kwargs = dict(kwargs)
        param_names = [p.name for p in plugin.parameters]
        for i, arg_val in enumerate(args):
            if i < len(param_names):
                all_kwargs.setdefault(param_names[i], arg_val)

        return plugin.execute_validated(str(input_text), **all_kwargs)

    def list_plugins(self, category: Optional[str] = None) -> List[TransformPlugin]:
        plugins = list(self._plugins.values())
        if category is not None:
            plugins = [p for p in plugins if p.category == category]
        return sorted(plugins, key=lambda p: p.name)

    def plugin_names(self) -> List[str]:
        return sorted(self._plugins.keys())

    def categories(self) -> List[str]:
        return sorted({p.category for p in self._plugins.values()})

    def __contains__(self, name: str) -> bool:
        return name in self._plugins

    def __iter__(self) -> Iterator[TransformPlugin]:
        return iter(self._plugins.values())

    def __len__(self) -> int:
        return len(self._plugins)
