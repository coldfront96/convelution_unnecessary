"""
HTTP API for STRATUM. Uses only stdlib http.server.

Endpoints:
  POST /transform          { "input": "...", "program": "..." }
  POST /disasm             { "program": "..." }
  GET  /plugins            list all plugins
  GET  /history?last=N     transformation history
  GET  /stats              aggregate statistics
  GET  /health             liveness check
"""

from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

logger = logging.getLogger(__name__)


def _json_response(handler: BaseHTTPRequestHandler, status: int, body: Any) -> None:
    payload = json.dumps(body, default=str).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    handler.wfile.write(payload)


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


class StratumRequestHandler(BaseHTTPRequestHandler):
    # These are injected by the factory that creates the server
    orchestrator: Any = None
    registry: Any = None
    history_projection: Any = None
    stats_projection: Any = None

    def log_message(self, format: str, *args: Any) -> None:
        logger.debug("HTTP %s %s %s", self.address_string(), self.command, self.path)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        qs = parse_qs(parsed.query)

        if path == "/health":
            _json_response(self, 200, {"status": "ok", "service": "stratum"})

        elif path == "/plugins":
            plugins = self.registry.list_plugins()
            _json_response(self, 200, {
                "plugins": [
                    {
                        "name": p.name,
                        "description": p.description,
                        "category": p.category,
                        "parameters": [
                            {"name": pa.name, "type": pa.type, "default": pa.default}
                            for pa in p.parameters
                        ],
                    }
                    for p in plugins
                ]
            })

        elif path == "/history":
            n = int(qs.get("last", ["10"])[0])
            records = self.history_projection.last(n)
            _json_response(self, 200, {
                "records": [
                    {
                        "session_id": r.session_id,
                        "input": r.input_text,
                        "output": r.output_text,
                        "program": r.program_source,
                        "succeeded": r.succeeded,
                        "duration_ms": r.duration_ms,
                        "error": r.error_message,
                    }
                    for r in records
                ]
            })

        elif path == "/stats":
            g = self.stats_projection.global_stats()
            top = self.stats_projection.top_plugins(10)
            _json_response(self, 200, {
                "global": {
                    "total_transformations": g.total_transformations,
                    "successful": g.successful_transformations,
                    "failed": g.failed_transformations,
                    "avg_duration_ms": g.avg_duration_ms,
                    "total_instructions": g.total_instructions_executed,
                },
                "top_plugins": [
                    {
                        "name": ps.plugin_name,
                        "calls": ps.call_count,
                        "avg_duration_us": ps.avg_duration_us,
                        "success_rate": ps.success_rate,
                    }
                    for ps in top
                ],
            })

        else:
            _json_response(self, 404, {"error": f"Not found: {path}"})

    def do_POST(self) -> None:
        path = urlparse(self.path).path.rstrip("/")
        body = _read_json_body(self)

        if path == "/transform":
            input_text = body.get("input", "")
            program = body.get("program", "")
            if not program:
                _json_response(self, 400, {"error": "program is required"})
                return
            result = self.orchestrator.transform(input_text, program)
            if result.is_ok():
                _json_response(self, 200, {"output": result.unwrap()})
            else:
                _json_response(self, 422, {"error": result.unwrap_err()})

        elif path == "/disasm":
            program = body.get("program", "")
            if not program:
                _json_response(self, 400, {"error": "program is required"})
                return
            result = self.orchestrator.disassemble(program)
            if result.is_ok():
                _json_response(self, 200, {"disassembly": result.unwrap()})
            else:
                _json_response(self, 422, {"error": result.unwrap_err()})

        else:
            _json_response(self, 404, {"error": f"Not found: {path}"})


def create_http_server(
    orchestrator: Any,
    registry: Any,
    history_projection: Any,
    stats_projection: Any,
    host: str = "127.0.0.1",
    port: int = 8742,
) -> HTTPServer:
    """Factory that creates a configured HTTPServer."""

    # Inject dependencies into the handler class via a subclass
    handler_class = type(
        "BoundStratumHandler",
        (StratumRequestHandler,),
        {
            "orchestrator": orchestrator,
            "registry": registry,
            "history_projection": history_projection,
            "stats_projection": stats_projection,
        },
    )
    server = HTTPServer((host, port), handler_class)
    return server
