"""Tests for hot-reloadable external plugin system."""

import os
import textwrap
import time
from pathlib import Path
from unittest.mock import MagicMock, call

import pytest

from stratum.core.events import EventBus, EventStore, ExternalPluginLoaded, ExternalPluginReloaded
from stratum.plugins.loader import ExternalPluginLoader, PluginLoadError
from stratum.plugins.registry import PluginRegistry
from stratum.plugins.watcher import PluginDirectoryWatcher


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_registry():
    bus = EventBus()
    store = EventStore(db_path=":memory:")
    return PluginRegistry(event_bus=bus, event_store=store), bus


def _write_plugin(path: Path, plugin_name: str, transform_body: str = "return Ok(input_text.upper())") -> None:
    path.write_text(textwrap.dedent(f"""
        from stratum.plugins.base import TransformPlugin
        from stratum.core.result import Ok, Result

        class TestPlugin_{plugin_name.replace('-','_')}(TransformPlugin):
            @property
            def name(self) -> str:
                return "{plugin_name}"

            @property
            def description(self) -> str:
                return "Test plugin {plugin_name}"

            def execute(self, input_text: str, **kwargs) -> Result:
                {transform_body}
    """).strip())


# ---------------------------------------------------------------------------
# ExternalPluginLoader tests
# ---------------------------------------------------------------------------


def test_load_from_file_basic(tmp_path):
    py = tmp_path / "myplugin.py"
    _write_plugin(py, "test_upper")
    plugins = ExternalPluginLoader.load_from_file(py)
    assert len(plugins) == 1
    assert plugins[0].name == "test_upper"


def test_load_from_file_executes_correctly(tmp_path):
    py = tmp_path / "myplugin.py"
    _write_plugin(py, "my_reverse", "return Ok(input_text[::-1])")
    plugins = ExternalPluginLoader.load_from_file(py)
    assert len(plugins) == 1
    result = plugins[0].execute("hello")
    assert result.unwrap() == "olleh"


def test_load_from_file_multiple_plugins(tmp_path):
    py = tmp_path / "multi.py"
    py.write_text(textwrap.dedent("""
        from stratum.plugins.base import TransformPlugin
        from stratum.core.result import Ok, Result

        class PluginA(TransformPlugin):
            @property
            def name(self): return "plugin_a"
            @property
            def description(self): return "A"
            def execute(self, input_text, **kw): return Ok(input_text + "_a")

        class PluginB(TransformPlugin):
            @property
            def name(self): return "plugin_b"
            @property
            def description(self): return "B"
            def execute(self, input_text, **kw): return Ok(input_text + "_b")
    """).strip())
    plugins = ExternalPluginLoader.load_from_file(py)
    names = {p.name for p in plugins}
    assert names == {"plugin_a", "plugin_b"}


def test_load_from_file_skips_abstract_base(tmp_path):
    py = tmp_path / "abstract.py"
    py.write_text(textwrap.dedent("""
        from stratum.plugins.base import TransformPlugin
        from stratum.core.result import Ok

        class ConcretePlugin(TransformPlugin):
            @property
            def name(self): return "concrete_only"
            @property
            def description(self): return "only concrete"
            def execute(self, input_text, **kw): return Ok(input_text)
    """).strip())
    plugins = ExternalPluginLoader.load_from_file(py)
    assert all(p.name != "TransformPlugin" for p in plugins)
    assert len(plugins) == 1


def test_load_from_file_missing_raises(tmp_path):
    with pytest.raises(PluginLoadError, match="not found"):
        ExternalPluginLoader.load_from_file(tmp_path / "nonexistent.py")


def test_load_from_file_non_py_raises(tmp_path):
    f = tmp_path / "plugin.txt"
    f.write_text("not a python file")
    with pytest.raises(PluginLoadError, match=r"\.py"):
        ExternalPluginLoader.load_from_file(f)


def test_load_from_file_syntax_error_raises(tmp_path):
    py = tmp_path / "bad.py"
    py.write_text("def broken(:::):")
    with pytest.raises(PluginLoadError):
        ExternalPluginLoader.load_from_file(py)


def test_load_from_dir_loads_all_py_files(tmp_path):
    _write_plugin(tmp_path / "p1.py", "ext_p1")
    _write_plugin(tmp_path / "p2.py", "ext_p2")
    plugins = ExternalPluginLoader.load_from_dir(tmp_path)
    names = {p.name for p in plugins}
    assert "ext_p1" in names
    assert "ext_p2" in names


def test_load_from_dir_missing_dir_returns_empty(tmp_path):
    plugins = ExternalPluginLoader.load_from_dir(tmp_path / "nonexistent")
    assert plugins == []


def test_load_from_dir_ignores_bad_files(tmp_path):
    _write_plugin(tmp_path / "good.py", "ext_good")
    bad = tmp_path / "bad.py"
    bad.write_text("this is not valid python !!!")
    plugins = ExternalPluginLoader.load_from_dir(tmp_path)
    assert any(p.name == "ext_good" for p in plugins)


def test_reload_returns_fresh_instance(tmp_path):
    py = tmp_path / "reload_me.py"
    _write_plugin(py, "reload_test", "return Ok(input_text + '_v1')")
    plugins_v1 = ExternalPluginLoader.load_from_file(py)
    assert plugins_v1[0].execute("x").unwrap() == "x_v1"

    # Overwrite the file with a new implementation
    _write_plugin(py, "reload_test", "return Ok(input_text + '_v2')")
    plugins_v2 = ExternalPluginLoader.load_from_file(py)
    assert plugins_v2[0].execute("x").unwrap() == "x_v2"


# ---------------------------------------------------------------------------
# PluginDirectoryWatcher tests
# ---------------------------------------------------------------------------


def test_watcher_loads_on_start(tmp_path):
    registry, bus = _make_registry()
    _write_plugin(tmp_path / "watch_p1.py", "watch_p1")
    watcher = PluginDirectoryWatcher([tmp_path], registry, bus, poll_interval=0.1)
    watcher.start()
    watcher.stop()
    assert "watch_p1" in registry


def test_watcher_publishes_loaded_event(tmp_path):
    registry, bus = _make_registry()
    _write_plugin(tmp_path / "event_test.py", "event_test_plugin")

    received = []
    bus.subscribe(ExternalPluginLoaded, received.append)

    watcher = PluginDirectoryWatcher([tmp_path], registry, bus, poll_interval=0.1)
    watcher.start()
    watcher.stop()

    assert any(e.plugin_name == "event_test_plugin" for e in received)


def test_watcher_detects_new_file(tmp_path):
    registry, bus = _make_registry()
    watcher = PluginDirectoryWatcher([tmp_path], registry, bus, poll_interval=0.05)
    watcher.start()

    # Write a new plugin AFTER the watcher has started
    time.sleep(0.01)
    _write_plugin(tmp_path / "late_arrival.py", "late_arrival")
    time.sleep(0.2)  # wait for poll cycle

    watcher.stop()
    assert "late_arrival" in registry


def test_watcher_detects_changed_file(tmp_path):
    registry, bus = _make_registry()
    py = tmp_path / "changing.py"
    _write_plugin(py, "changing_plugin", "return Ok(input_text + '_v1')")

    reloaded = []
    bus.subscribe(ExternalPluginReloaded, reloaded.append)

    watcher = PluginDirectoryWatcher([tmp_path], registry, bus, poll_interval=0.05)
    watcher.start()
    time.sleep(0.01)

    # Modify the file to trigger a reload
    _write_plugin(py, "changing_plugin", "return Ok(input_text + '_v2')")
    # Bump mtime to ensure detection (some FSes have 1s resolution)
    new_mtime = time.time() + 1
    os.utime(py, (new_mtime, new_mtime))

    time.sleep(0.3)
    watcher.stop()

    plugin = registry.get("changing_plugin")
    assert plugin is not None
    assert plugin.execute("x").unwrap() == "x_v2"


def test_watcher_not_running_after_stop(tmp_path):
    registry, bus = _make_registry()
    watcher = PluginDirectoryWatcher([tmp_path], registry, bus, poll_interval=0.05)
    watcher.start()
    assert watcher.is_running
    watcher.stop()
    assert not watcher.is_running


def test_watcher_start_idempotent(tmp_path):
    registry, bus = _make_registry()
    watcher = PluginDirectoryWatcher([tmp_path], registry, bus, poll_interval=0.1)
    watcher.start()
    watcher.start()  # second call should be a no-op
    assert watcher.is_running
    watcher.stop()


def test_watcher_force_scan(tmp_path):
    registry, bus = _make_registry()
    watcher = PluginDirectoryWatcher([tmp_path], registry, bus, poll_interval=999)
    # Don't start thread — manually drive scanning
    _write_plugin(tmp_path / "manual.py", "manual_scan_plugin")
    count = watcher.force_scan()
    assert count >= 1
    assert "manual_scan_plugin" in registry


def test_watcher_missing_dir_does_not_crash(tmp_path):
    registry, bus = _make_registry()
    watcher = PluginDirectoryWatcher([tmp_path / "nodir"], registry, bus, poll_interval=0.1)
    watcher.start()
    watcher.stop()


# ---------------------------------------------------------------------------
# Integration: loader + registry + execution
# ---------------------------------------------------------------------------


def test_external_plugin_can_be_invoked_via_registry(tmp_path):
    py = tmp_path / "inv.py"
    _write_plugin(py, "ext_shout", "return Ok(input_text.upper() + '!')")
    registry, bus = _make_registry()
    plugins = ExternalPluginLoader.load_from_file(py)
    registry.register_many(plugins)
    result = registry.invoke("ext_shout", "hello")
    assert result.is_ok()
    assert result.unwrap() == "HELLO!"


def test_external_plugin_with_parameters(tmp_path):
    py = tmp_path / "paramtest.py"
    py.write_text(textwrap.dedent("""
        from stratum.plugins.base import TransformPlugin, ParameterDef
        from stratum.core.result import Ok, Result
        from typing import List

        class RepeatPlugin(TransformPlugin):
            @property
            def name(self): return "ext_repeat"
            @property
            def description(self): return "Repeat input N times"
            @property
            def parameters(self) -> List[ParameterDef]:
                return [ParameterDef(name="times", type="integer", required=False, default=2)]
            def execute(self, input_text: str, times: int = 2, **kw):
                return Ok(input_text * times)
    """).strip())
    registry, bus = _make_registry()
    plugins = ExternalPluginLoader.load_from_file(py)
    registry.register_many(plugins)
    result = registry.invoke("ext_repeat", "ab", times=3)
    assert result.unwrap() == "ababab"
