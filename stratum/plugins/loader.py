"""
External plugin loader.

Dynamically imports .py files from user-specified directories and
discovers TransformPlugin subclasses inside them.

Uses importlib.util so no sys.path mutation is needed.

Pattern: Factory Method (load_from_file returns a list of plugin instances).
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
from pathlib import Path
from typing import List

from stratum.plugins.base import TransformPlugin

logger = logging.getLogger(__name__)


class PluginLoadError(Exception):
    pass


class ExternalPluginLoader:
    """
    Loads TransformPlugin subclasses from arbitrary .py files on disk.
    Each file gets its own module namespace keyed by a stable module name
    derived from the file path so reloads replace the previous module.
    """

    @staticmethod
    def _module_name(path: Path) -> str:
        """Stable module name for a given file path (no collisions across dirs)."""
        return f"_stratum_ext_{path.stem}_{abs(hash(str(path.resolve())))}"

    @classmethod
    def load_from_file(cls, path: Path) -> List[TransformPlugin]:
        """
        Dynamically import *path* and return all TransformPlugin subclasses
        found in its module namespace.  Re-imports if the module was previously
        loaded (hot-reload semantics).

        Compiles source directly (bypasses .pyc cache) so reloads always
        reflect the latest file content.
        """
        path = path.resolve()
        if not path.exists():
            raise PluginLoadError(f"Plugin file not found: {path}")
        if path.suffix != ".py":
            raise PluginLoadError(f"Expected a .py file, got: {path}")

        module_name = cls._module_name(path)

        # Read and compile from source directly — bypasses Python's .pyc
        # bytecode cache, which would otherwise return stale code on rapid
        # rewrites (mtime resolution can be 1 s on some filesystems).
        try:
            source = path.read_text(encoding="utf-8")
            code = compile(source, str(path), "exec")
        except SyntaxError as exc:
            raise PluginLoadError(f"Syntax error in {path}: {exc}") from exc
        except OSError as exc:
            raise PluginLoadError(f"Cannot read {path}: {exc}") from exc

        spec = importlib.util.spec_from_file_location(module_name, path)
        if spec is None:
            raise PluginLoadError(f"Cannot create module spec for {path}")

        module = importlib.util.module_from_spec(spec)
        module.__file__ = str(path)
        module.__name__ = module_name
        # Replace cached entry so subsequent imports of this module name see
        # the new version rather than an older one.
        sys.modules[module_name] = module

        try:
            exec(code, module.__dict__)  # noqa: S102
        except Exception as exc:
            raise PluginLoadError(f"Error executing plugin file {path}: {exc}") from exc

        plugins: List[TransformPlugin] = []
        for _name, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, TransformPlugin)
                and obj is not TransformPlugin
                and not inspect.isabstract(obj)
                and obj.__module__ == module_name
            ):
                try:
                    instance = obj()
                    plugins.append(instance)
                    logger.debug("loaded external plugin %r from %s", instance.name, path)
                except Exception as exc:
                    logger.warning("could not instantiate plugin class %r from %s: %s", _name, path, exc)

        return plugins

    @classmethod
    def load_from_dir(cls, directory: Path) -> List[TransformPlugin]:
        """Load all .py files in *directory* (non-recursive)."""
        directory = directory.resolve()
        if not directory.is_dir():
            logger.warning("external plugin dir does not exist: %s", directory)
            return []

        plugins: List[TransformPlugin] = []
        for py_file in sorted(directory.glob("*.py")):
            try:
                plugins.extend(cls.load_from_file(py_file))
            except PluginLoadError as exc:
                logger.warning("skipping %s: %s", py_file.name, exc)

        return plugins
