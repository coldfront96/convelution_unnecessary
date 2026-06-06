"""
Command-line interface for STRATUM.

Commands:
    python -m stratum <input> <program>       — run a transformation
    python -m stratum disasm <program>        — show bytecode disassembly
    python -m stratum typecheck <program>     — run static type analysis
    python -m stratum plugins                 — list available plugins
    python -m stratum history [--last N]      — show transformation history
    python -m stratum stats                   — show aggregate statistics
    python -m stratum query "<HQL>"           — query history with HQL
    python -m stratum serve                   — start HTTP API server
"""

from __future__ import annotations

import sys
from typing import List, Optional

from stratum.core.result import Result


class CliError(Exception):
    pass


def run_cli(
    args: List[str],
    orchestrator,
    registry,
    history_projection,
    stats_projection,
    http_server_factory,
    config,
    query_executor=None,
) -> int:
    """
    Entry point. Returns exit code.
    """
    if not args:
        _print_usage()
        return 0

    cmd = args[0]

    if cmd in ("--help", "-h", "help"):
        _print_usage()
        return 0

    if cmd == "plugins":
        return _cmd_plugins(registry, args[1:])

    if cmd == "history":
        return _cmd_history(history_projection, args[1:])

    if cmd == "stats":
        return _cmd_stats(stats_projection)

    if cmd == "serve":
        return _cmd_serve(http_server_factory, config)

    if cmd == "disasm":
        if len(args) < 2:
            print("Usage: stratum disasm <program>", file=sys.stderr)
            return 1
        return _cmd_disasm(orchestrator, args[1])

    if cmd == "typecheck":
        if len(args) < 2:
            print("Usage: stratum typecheck <program>", file=sys.stderr)
            return 1
        return _cmd_typecheck(orchestrator, args[1])

    if cmd == "query":
        if len(args) < 2:
            print('Usage: stratum query "<HQL>"', file=sys.stderr)
            return 1
        executor = query_executor
        if executor is None:
            from stratum.query.executor import QueryExecutor
            executor = QueryExecutor(history_projection)
        return _cmd_query(executor, args[1])

    # Default: transform
    if len(args) < 2:
        print("Usage: stratum <input_text> <program>", file=sys.stderr)
        return 1

    return _cmd_transform(orchestrator, args[0], args[1])


def _cmd_transform(orchestrator, input_text: str, program: str) -> int:
    result = orchestrator.transform(input_text, program)
    if result.is_ok():
        print(result.unwrap())
        return 0
    print(f"Error: {result.unwrap_err()}", file=sys.stderr)
    return 1


def _cmd_disasm(orchestrator, program: str) -> int:
    result = orchestrator.disassemble(program)
    if result.is_ok():
        print(result.unwrap())
        return 0
    print(f"Error: {result.unwrap_err()}", file=sys.stderr)
    return 1


def _cmd_typecheck(orchestrator, program: str) -> int:
    result = orchestrator.typecheck(program)
    if result.is_ok():
        print(result.unwrap())
        return 0
    print(f"Error: {result.unwrap_err()}", file=sys.stderr)
    return 1


def _cmd_query(query_executor, hql: str) -> int:
    from stratum.query.executor import QueryError
    try:
        result = query_executor.execute(hql)
        print(result.to_table())
        return 0
    except QueryError as exc:
        print(f"HQL Error: {exc}", file=sys.stderr)
        return 1


def _cmd_plugins(registry, args: List[str]) -> int:
    category_filter: Optional[str] = None
    if "--category" in args:
        idx = args.index("--category")
        if idx + 1 < len(args):
            category_filter = args[idx + 1]

    plugins = registry.list_plugins(category=category_filter)
    if not plugins:
        print("No plugins registered.")
        return 0

    categories: dict[str, list] = {}
    for p in plugins:
        categories.setdefault(p.category, []).append(p)

    for cat, cat_plugins in sorted(categories.items()):
        print(f"\n{cat.upper()}")
        print("-" * 40)
        for p in sorted(cat_plugins, key=lambda x: x.name):
            param_str = ""
            if p.parameters:
                param_str = "  params: " + ", ".join(
                    f"{pa.name}={pa.default!r}" for pa in p.parameters
                )
            print(f"  {p.name:<20} {p.description}{param_str}")

    print(f"\nTotal: {len(plugins)} plugins")
    return 0


def _cmd_history(history_projection, args: List[str]) -> int:
    n = 10
    if "--last" in args:
        idx = args.index("--last")
        if idx + 1 < len(args):
            try:
                n = int(args[idx + 1])
            except ValueError:
                pass

    records = history_projection.last(n)
    if not records:
        print("No transformation history.")
        return 0

    for i, r in enumerate(records, 1):
        status = "OK" if r.succeeded else "FAIL"
        dur = f"{r.duration_ms:.1f}ms"
        print(f"{i:3}. [{status}] {dur:>8}  {r.program_source!r}")
        if r.succeeded:
            print(f"        {r.input_text!r} → {r.output_text!r}")
        else:
            print(f"        ERROR: {r.error_message}")
    return 0


def _cmd_stats(stats_projection) -> int:
    g = stats_projection.global_stats()
    print("=== Global Statistics ===")
    print(f"  Transformations : {g.total_transformations}")
    print(f"  Successful      : {g.successful_transformations}")
    print(f"  Failed          : {g.failed_transformations}")
    print(f"  Avg duration    : {g.avg_duration_ms:.2f}ms")
    print(f"  Total instrs    : {g.total_instructions_executed}")

    top = stats_projection.top_plugins(10)
    if top:
        print("\n=== Top Plugins by Call Count ===")
        for ps in top:
            print(
                f"  {ps.plugin_name:<20} calls={ps.call_count:<6} "
                f"avg={ps.avg_duration_us:.0f}µs  success={ps.success_rate:.0%}"
            )
    return 0


def _cmd_serve(http_server_factory, config) -> int:
    server = http_server_factory()
    host = config.api.http_host
    port = config.api.http_port
    print(f"STRATUM HTTP API listening on http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
    return 0


def _print_usage() -> None:
    print("""
STRATUM — Unnecessarily Complex String Transformation Pipeline

Usage:
  stratum <input> <program>          Transform input through a STL program
  stratum disasm <program>           Show bytecode disassembly
  stratum typecheck <program>        Static type analysis of a STL program
  stratum plugins [--category CAT]   List all registered plugins
  stratum history [--last N]         Show transformation history (default: 10)
  stratum stats                      Show aggregate statistics
  stratum query "<HQL>"              Query history with HQL
  stratum serve                      Start the HTTP API server

STL Quick Reference:
  upper | reverse                    Pipeline: apply transforms left to right
  pad_left(width=20, char=".")       Named arguments
  if length > 10 then truncate(10) else upper   Conditional
  let x = length; repeat(x)         Variable binding
  macro shout = upper | append("!")  Macro definition

Examples:
  stratum "hello world" "upper | reverse"
  stratum "hello world" "if length > 5 then upper else lower"
  stratum "hello" "repeat(times=3) | join(sep='-')"
""".strip())
