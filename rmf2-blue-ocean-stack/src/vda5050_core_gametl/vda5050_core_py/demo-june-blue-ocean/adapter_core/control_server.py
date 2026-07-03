"""Small adapter-local control plane for demo recovery actions."""

from __future__ import annotations

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


class _AdapterControlHandler(BaseHTTPRequestHandler):
    adapter: Any = None

    def do_GET(self) -> None:
        if self.path != "/health":
            self._send_json(404, {"success": False, "error": "not_found"})
            return
        self._send_json(200, {"success": True})

    def do_POST(self) -> None:
        if self.path != "/order-state/reset":
            self._send_json(404, {"success": False, "error": "not_found"})
            return

        try:
            if self.adapter is None:
                raise RuntimeError("adapter not bound")
            self.adapter.reset_order_state()
        except Exception as exc:  # pragma: no cover - exercised in live demo
            self._send_json(
                500,
                {
                    "success": False,
                    "error": type(exc).__name__,
                    "detail": str(exc),
                },
            )
            return

        self._send_json(200, {"success": True})

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[adapter-control] {fmt % args}", file=sys.stderr)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_control_server(
    adapter: Any,
    *,
    host: str | None = None,
    port: int | None = None,
) -> ThreadingHTTPServer | None:
    """Start the adapter control server in a daemon thread.

    Set ``ADAPTER_CONTROL_PORT=0`` to disable it.
    """
    bind_host = host if host is not None else os.environ.get(
        "ADAPTER_CONTROL_HOST", "0.0.0.0"
    )
    bind_port = (
        int(port)
        if port is not None
        else int(os.environ.get("ADAPTER_CONTROL_PORT", "8099"))
    )
    if bind_port <= 0:
        return None

    handler = type(
        "AdapterControlHandler",
        (_AdapterControlHandler,),
        {"adapter": adapter},
    )
    server = ThreadingHTTPServer((bind_host, bind_port), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(
        f"Adapter control server listening on {bind_host}:{bind_port}",
        file=sys.stderr,
    )
    return server
