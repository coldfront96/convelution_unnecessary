"""
Configuration loading with schema validation.

Config lives in stratum.toml. A hand-rolled schema validator
ensures all values are present and correctly typed before
anything else touches them.

Pattern: Template Method (SchemaNode.validate dispatches to subclass).
"""

from __future__ import annotations

import logging
import tomllib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


# ---- schema DSL -------------------------------------------------------------


class SchemaNode(ABC):
    def __init__(self, required: bool = True, default: Any = None) -> None:
        self.required = required
        self.default = default

    @abstractmethod
    def validate(self, value: Any, path: str) -> tuple[Any, list[str]]:
        """Return (coerced_value, list_of_errors)."""
        ...


class StringNode(SchemaNode):
    def __init__(self, choices: Optional[list[str]] = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.choices = choices

    def validate(self, value: Any, path: str) -> tuple[Any, list[str]]:
        if not isinstance(value, str):
            return value, [f"{path}: expected string, got {type(value).__name__}"]
        if self.choices and value not in self.choices:
            return value, [f"{path}: '{value}' not in allowed values {self.choices}"]
        return value, []


class IntNode(SchemaNode):
    def __init__(self, min_val: Optional[int] = None, max_val: Optional[int] = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.min_val = min_val
        self.max_val = max_val

    def validate(self, value: Any, path: str) -> tuple[Any, list[str]]:
        if not isinstance(value, int):
            return value, [f"{path}: expected int, got {type(value).__name__}"]
        errors: list[str] = []
        if self.min_val is not None and value < self.min_val:
            errors.append(f"{path}: {value} < minimum {self.min_val}")
        if self.max_val is not None and value > self.max_val:
            errors.append(f"{path}: {value} > maximum {self.max_val}")
        return value, errors


class BoolNode(SchemaNode):
    def validate(self, value: Any, path: str) -> tuple[Any, list[str]]:
        if not isinstance(value, bool):
            return value, [f"{path}: expected bool, got {type(value).__name__}"]
        return value, []


class TableNode(SchemaNode):
    def __init__(self, children: Dict[str, SchemaNode], **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.children = children

    def validate(self, value: Any, path: str) -> tuple[Any, list[str]]:
        if not isinstance(value, dict):
            return value, [f"{path}: expected table, got {type(value).__name__}"]
        errors: list[str] = []
        result: dict[str, Any] = dict(value)
        for key, node in self.children.items():
            child_path = f"{path}.{key}" if path else key
            if key not in value:
                if node.required:
                    errors.append(f"{child_path}: required key missing")
                else:
                    result[key] = node.default
            else:
                coerced, child_errors = node.validate(value[key], child_path)
                result[key] = coerced
                errors.extend(child_errors)
        return result, errors


class ListNode(SchemaNode):
    def __init__(self, item_node: SchemaNode, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.item_node = item_node

    def validate(self, value: Any, path: str) -> tuple[Any, list[str]]:
        if not isinstance(value, list):
            return value, [f"{path}: expected list, got {type(value).__name__}"]
        errors: list[str] = []
        result: list[Any] = []
        for i, item in enumerate(value):
            coerced, item_errors = self.item_node.validate(item, f"{path}[{i}]")
            result.append(coerced)
            errors.extend(item_errors)
        return result, errors


# ---- config schema ----------------------------------------------------------


_SCHEMA = TableNode(
    children={
        "vm": TableNode(
            children={
                "register_count": IntNode(min_val=4, max_val=64, required=False, default=16),
                "max_call_depth": IntNode(min_val=10, max_val=1000, required=False, default=100),
                "max_instructions": IntNode(min_val=100, required=False, default=100_000),
                "emit_instruction_events": BoolNode(required=False, default=False),
            },
            required=False,
            default={},
        ),
        "optimizer": TableNode(
            children={
                "enabled": BoolNode(required=False, default=True),
                "passes": ListNode(
                    StringNode(choices=["constant_fold", "dead_code", "peephole", "pipeline_merge"]),
                    required=False,
                    default=["constant_fold", "dead_code", "peephole", "pipeline_merge"],
                ),
                "max_iterations": IntNode(min_val=1, max_val=20, required=False, default=3),
            },
            required=False,
            default={},
        ),
        "plugins": TableNode(
            children={
                "load_builtins": BoolNode(required=False, default=True),
                "extra_plugin_dirs": ListNode(StringNode(), required=False, default=[]),
            },
            required=False,
            default={},
        ),
        "storage": TableNode(
            children={
                "db_path": StringNode(required=False, default="stratum.db"),
                "in_memory": BoolNode(required=False, default=False),
            },
            required=False,
            default={},
        ),
        "api": TableNode(
            children={
                "http_host": StringNode(required=False, default="127.0.0.1"),
                "http_port": IntNode(min_val=1024, max_val=65535, required=False, default=8742),
                "request_timeout_s": IntNode(min_val=1, max_val=300, required=False, default=30),
            },
            required=False,
            default={},
        ),
        "logging": TableNode(
            children={
                "level": StringNode(
                    choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
                    required=False,
                    default="INFO",
                ),
                "format": StringNode(required=False, default="%(asctime)s %(levelname)s %(name)s: %(message)s"),
            },
            required=False,
            default={},
        ),
    }
)


# ---- config dataclass -------------------------------------------------------


@dataclass
class VmConfig:
    register_count: int = 16
    max_call_depth: int = 100
    max_instructions: int = 100_000
    emit_instruction_events: bool = False


@dataclass
class OptimizerConfig:
    enabled: bool = True
    passes: list[str] = field(default_factory=lambda: ["constant_fold", "dead_code", "peephole", "pipeline_merge"])
    max_iterations: int = 3


@dataclass
class PluginsConfig:
    load_builtins: bool = True
    extra_plugin_dirs: list[str] = field(default_factory=list)


@dataclass
class StorageConfig:
    db_path: str = "stratum.db"
    in_memory: bool = False


@dataclass
class ApiConfig:
    http_host: str = "127.0.0.1"
    http_port: int = 8742
    request_timeout_s: int = 30


@dataclass
class LoggingConfig:
    level: str = "INFO"
    format: str = "%(asctime)s %(levelname)s %(name)s: %(message)s"


@dataclass
class Config:
    vm: VmConfig = field(default_factory=VmConfig)
    optimizer: OptimizerConfig = field(default_factory=OptimizerConfig)
    plugins: PluginsConfig = field(default_factory=PluginsConfig)
    storage: StorageConfig = field(default_factory=StorageConfig)
    api: ApiConfig = field(default_factory=ApiConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Config":
        return cls(
            vm=VmConfig(**data.get("vm", {})),
            optimizer=OptimizerConfig(**data.get("optimizer", {})),
            plugins=PluginsConfig(**data.get("plugins", {})),
            storage=StorageConfig(**data.get("storage", {})),
            api=ApiConfig(**data.get("api", {})),
            logging=LoggingConfig(**data.get("logging", {})),
        )

    def effective_db_path(self) -> str:
        if self.storage.in_memory:
            return ":memory:"
        return self.storage.db_path


class ConfigValidationError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("Config validation failed:\n" + "\n".join(f"  - {e}" for e in errors))


def load_config(path: Optional[Union[str, Path]] = None) -> Config:
    """
    Load and validate config from a TOML file.
    Falls back to defaults if no path given or file not found.
    """
    raw: dict[str, Any] = {}

    if path is not None:
        p = Path(path)
        if p.exists():
            with open(p, "rb") as f:
                raw = tomllib.load(f)
            logger.info("loaded config from %s", p)
        else:
            logger.warning("config file not found at %s, using defaults", p)

    coerced, errors = _SCHEMA.validate(raw, "")
    if errors:
        raise ConfigValidationError(errors)

    return Config.from_dict(coerced)
