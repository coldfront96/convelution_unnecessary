"""
Dependency injection container.

Components declare their dependencies via constructor type hints.
The container resolves and injects them automatically.
No component constructs its own dependencies.

Patterns: Abstract Factory (resolves factories), Singleton/Transient scopes.
"""

from __future__ import annotations

import inspect
import logging
import threading
from enum import Enum, auto
from typing import Any, Callable, Dict, Optional, Type, TypeVar, get_type_hints

logger = logging.getLogger(__name__)

T = TypeVar("T")


class Scope(Enum):
    SINGLETON = auto()   # one instance per container
    TRANSIENT = auto()   # new instance every resolve


class ContainerError(Exception):
    pass


class CircularDependencyError(ContainerError):
    pass


class UnresolvableError(ContainerError):
    pass


class _Registration:
    __slots__ = ("implementation", "factory", "instance", "scope")

    def __init__(
        self,
        implementation: Optional[Type],
        factory: Optional[Callable],
        instance: Optional[Any],
        scope: Scope,
    ) -> None:
        self.implementation = implementation
        self.factory = factory
        self.instance = instance
        self.scope = scope


class Container:
    """
    Inversion-of-control container.

    Usage:
        container = Container()
        container.register(IFoo, FooImpl)          # transient by default
        container.register(IBar, BarImpl, Scope.SINGLETON)
        container.register_instance(IConfig, cfg)
        container.register_factory(IWorker, lambda: Worker(os.cpu_count()))

        foo = container.resolve(IFoo)  # FooImpl with all deps injected
    """

    def __init__(self) -> None:
        self._registrations: Dict[type, _Registration] = {}
        self._resolving: set[type] = set()  # cycle detection
        self._lock = threading.RLock()

    # ---- registration -------------------------------------------------------

    def register(
        self,
        interface: Type[T],
        implementation: Type[T],
        scope: Scope = Scope.SINGLETON,
    ) -> "Container":
        with self._lock:
            self._registrations[interface] = _Registration(
                implementation=implementation,
                factory=None,
                instance=None,
                scope=scope,
            )
        logger.debug("registered %s -> %s (%s)", interface.__name__, implementation.__name__, scope.name)
        return self  # fluent API

    def register_instance(self, interface: Type[T], instance: T) -> "Container":
        """Register a pre-built instance. Always singleton-scoped."""
        with self._lock:
            self._registrations[interface] = _Registration(
                implementation=None,
                factory=None,
                instance=instance,
                scope=Scope.SINGLETON,
            )
        logger.debug("registered instance for %s", interface.__name__)
        return self

    def register_factory(
        self,
        interface: Type[T],
        factory: Callable[[], T],
        scope: Scope = Scope.TRANSIENT,
    ) -> "Container":
        with self._lock:
            self._registrations[interface] = _Registration(
                implementation=None,
                factory=factory,
                instance=None,
                scope=scope,
            )
        logger.debug("registered factory for %s (%s)", interface.__name__, scope.name)
        return self

    def register_self(self, cls: Type[T], scope: Scope = Scope.SINGLETON) -> "Container":
        """Register a concrete class against itself."""
        return self.register(cls, cls, scope)

    # ---- resolution ---------------------------------------------------------

    def resolve(self, interface: Type[T]) -> T:
        """
        Resolve an interface to a concrete instance.
        Recursively resolves constructor parameters via type hints.
        """
        with self._lock:
            return self._resolve_locked(interface)

    def _resolve_locked(self, interface: Type[T]) -> T:
        if interface in self._resolving:
            chain = " -> ".join(t.__name__ for t in self._resolving)
            raise CircularDependencyError(
                f"Circular dependency detected: {chain} -> {interface.__name__}"
            )

        reg = self._registrations.get(interface)

        # No registration — try to self-register if it's a concrete class
        if reg is None:
            if inspect.isclass(interface) and not inspect.isabstract(interface):
                logger.debug(
                    "auto-registering unregistered concrete class %s", interface.__name__
                )
                self._registrations[interface] = _Registration(
                    implementation=interface,
                    factory=None,
                    instance=None,
                    scope=Scope.SINGLETON,
                )
                reg = self._registrations[interface]
            else:
                raise UnresolvableError(
                    f"No registration found for {interface!r}. "
                    "Register it before resolving."
                )

        # Already-built singleton
        if reg.scope == Scope.SINGLETON and reg.instance is not None:
            return reg.instance  # type: ignore[return-value]

        self._resolving.add(interface)
        try:
            instance = self._build(reg)
        finally:
            self._resolving.discard(interface)

        if reg.scope == Scope.SINGLETON:
            reg.instance = instance

        return instance  # type: ignore[return-value]

    def _build(self, reg: _Registration) -> Any:
        if reg.instance is not None:
            return reg.instance

        if reg.factory is not None:
            return reg.factory()

        assert reg.implementation is not None
        return self._construct(reg.implementation)

    def _construct(self, cls: Type) -> Any:
        """Inspect __init__ type hints and inject each parameter."""
        try:
            hints = get_type_hints(cls.__init__)
        except Exception:
            hints = {}

        hints.pop("return", None)

        sig = inspect.signature(cls.__init__)
        kwargs: dict[str, Any] = {}

        for param_name, param in sig.parameters.items():
            if param_name == "self":
                continue
            if param_name not in hints:
                if param.default is inspect.Parameter.empty:
                    raise UnresolvableError(
                        f"Cannot inject {cls.__name__}.__init__ param "
                        f"'{param_name}': no type hint and no default."
                    )
                continue  # use default

            dep_type = hints[param_name]
            try:
                kwargs[param_name] = self._resolve_locked(dep_type)
            except UnresolvableError:
                if param.default is not inspect.Parameter.empty:
                    pass  # use default
                else:
                    raise

        logger.debug("constructing %s with kwargs: %s", cls.__name__, list(kwargs.keys()))
        return cls(**kwargs)

    # ---- introspection ------------------------------------------------------

    def is_registered(self, interface: Type) -> bool:
        return interface in self._registrations

    def registered_types(self) -> list[type]:
        return list(self._registrations.keys())

    def reset(self) -> None:
        """Clear all registrations and singleton instances. Useful in tests."""
        with self._lock:
            self._registrations.clear()
            self._resolving.clear()
