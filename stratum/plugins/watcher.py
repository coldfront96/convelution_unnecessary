"""
Hot-reloadable plugin directory watcher.

Polls watched directories on a daemon thread. When a .py file's
modification time changes or a new .py file appears, the file is
re-imported and its plugins are re-registered (force=True).

Pattern: Observer (publishes ExternalPluginLoaded / ExternalPluginReloaded
         events onto the EventBus so other components can react),
         Strategy (poll_interval is configurable).
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional

from stratum.core.events import EventBus, ExternalPluginLoaded, ExternalPluginReloaded
from stratum.plugins.loader import ExternalPluginLoader, PluginLoadError
from stratum.plugins.registry import PluginRegistry

logger = logging.getLogger(__name__)


class PluginDirectoryWatcher:
    """
    Monitors one or more directories for new or changed .py plugin files.

    Call start() to begin watching on a background daemon thread.
    Call stop() to signal it to exit (it will stop within one poll cycle).
    """

    def __init__(
        self,
        directories: List[Path],
        registry: PluginRegistry,
        event_bus: EventBus,
        poll_interval: float = 1.0,
    ) -> None:
        self._dirs = [Path(d).resolve() for d in directories]
        self._registry = registry
        self._bus = event_bus
        self._poll_interval = poll_interval

        # path -> last known mtime
        self._mtimes: Dict[Path, float] = {}
        # path -> set of plugin names loaded from it (for reload tracking)
        self._file_plugins: Dict[Path, List[str]] = {}

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---- public API -----------------------------------------------------------

    def start(self) -> None:
        """Start the watcher thread. Safe to call multiple times (idempotent)."""
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        # Initial scan: load everything that already exists
        self._scan(initial=True)
        self._thread = threading.Thread(
            target=self._run,
            name="stratum-plugin-watcher",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "PluginDirectoryWatcher started (dirs=%s, interval=%.1fs)",
            [str(d) for d in self._dirs],
            self._poll_interval,
        )

    def stop(self) -> None:
        """Signal the watcher thread to stop."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=self._poll_interval * 2)
            self._thread = None
        logger.info("PluginDirectoryWatcher stopped")

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def force_scan(self) -> int:
        """Trigger a manual scan and return number of files processed."""
        return self._scan(initial=False)

    # ---- internals ------------------------------------------------------------

    def _run(self) -> None:
        while not self._stop_event.is_set():
            self._stop_event.wait(timeout=self._poll_interval)
            if not self._stop_event.is_set():
                self._scan(initial=False)

    def _scan(self, initial: bool) -> int:
        processed = 0
        for directory in self._dirs:
            if not directory.is_dir():
                continue
            for py_file in sorted(directory.glob("*.py")):
                try:
                    mtime = py_file.stat().st_mtime
                except OSError:
                    continue

                known_mtime = self._mtimes.get(py_file)

                if known_mtime is None:
                    # New file
                    self._load_file(py_file, mtime, is_reload=False)
                    processed += 1
                elif not initial and mtime != known_mtime:
                    # Changed file
                    self._load_file(py_file, mtime, is_reload=True)
                    processed += 1
                else:
                    self._mtimes[py_file] = mtime
        return processed

    def _load_file(self, path: Path, mtime: float, is_reload: bool) -> None:
        previous_names = list(self._file_plugins.get(path, []))
        try:
            plugins = ExternalPluginLoader.load_from_file(path)
        except PluginLoadError as exc:
            logger.error("failed to load plugin file %s: %s", path, exc)
            return

        self._mtimes[path] = mtime
        loaded_names: List[str] = []

        for plugin in plugins:
            action = "reloaded" if (is_reload and plugin.name in previous_names) else "loaded"
            self._registry.register(plugin, force=True)
            loaded_names.append(plugin.name)

            if action == "loaded":
                self._bus.publish(ExternalPluginLoaded(
                    plugin_name=plugin.name,
                    source_file=str(path),
                ))
            else:
                self._bus.publish(ExternalPluginReloaded(
                    plugin_name=plugin.name,
                    source_file=str(path),
                    previous_version=str(path),
                ))

            logger.info("%s external plugin %r from %s", action, plugin.name, path.name)

        self._file_plugins[path] = loaded_names
