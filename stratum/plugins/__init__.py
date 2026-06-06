"""Plugin system: base interface, registry, and built-in transform plugins."""

from stratum.plugins.base import TransformPlugin, ParameterDef, PluginError
from stratum.plugins.registry import PluginRegistry

__all__ = ["TransformPlugin", "ParameterDef", "PluginError", "PluginRegistry"]
