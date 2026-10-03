"""Test-only in-memory ModelOps API fake (ticket-060a): a real
``ThreadingHTTPServer`` on ``127.0.0.1:0``, started in a daemon thread
by its own context manager, so ``modelops_api.ModelOpsClient`` is
exercised against real localhost HTTP, real timeouts, and real
``urllib`` error handling -- not a mock. It serves ``workflows`` and
``tasks`` from two in-memory lists, records every request's method,
path and headers, and can be scripted to answer a given path with a
canned status, body and extra headers (for error, redirect and
redaction tests).

tickets 063 and 063a extend this fake with write endpoints; they
import it from here and never redefine it.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Any


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    path: str
    headers: dict[str, str]


@dataclass(frozen=True)
class _ScriptedResponse:
    status: int
    body: bytes
    headers: tuple[tuple[str, str], ...] = ()


class _Handler(BaseHTTPRequestHandler):
    server: FakeModelOpsServer

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def do_GET(self) -> None:
        self.server.handle_get(self)


class FakeModelOpsServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.workflows: list[dict[str, Any]] = []
        self.tasks: list[dict[str, Any]] = []
        self.requests: list[RecordedRequest] = []
        self._lock = threading.Lock()
        self._scripts: dict[str, _ScriptedResponse] = {}
        self._thread: threading.Thread | None = None

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"

    def script(
        self,
        path: str,
        *,
        status: int,
        body: bytes,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._scripts[path] = _ScriptedResponse(
            status, body, tuple((headers or {}).items())
        )

    def handle_get(self, handler: _Handler) -> None:
        with self._lock:
            self.requests.append(
                RecordedRequest(
                    method=handler.command,
                    path=handler.path,
                    headers=dict(handler.headers.items()),
                )
            )
            scripted = self._scripts.get(handler.path)
        if scripted is not None:
            self._respond(
                handler, scripted.status, scripted.body, scripted.headers
            )
            return
        payload = self._payload_for(handler.path)
        if payload is None:
            self._respond(handler, 404, b'{"error":"not found"}')
            return
        self._respond(handler, 200, json.dumps(payload).encode("utf-8"))

    def _payload_for(self, path: str) -> Any | None:
        if path == "/api/Workflow/all":
            return self.workflows
        if path == "/api/Task/all":
            return self.tasks
        if path.startswith("/api/Workflow/"):
            return self._find(
                self.workflows, path.removeprefix("/api/Workflow/")
            )
        if path.startswith("/api/Task/"):
            return self._find(self.tasks, path.removeprefix("/api/Task/"))
        return None

    @staticmethod
    def _find(
        items: list[dict[str, Any]], item_id: str
    ) -> dict[str, Any] | None:
        for item in items:
            if item.get("_id") == item_id:
                return item
        return None

    @staticmethod
    def _respond(
        handler: _Handler,
        status: int,
        body: bytes,
        headers: tuple[tuple[str, str], ...] = (),
    ) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        for key, value in headers:
            handler.send_header(key, value)
        handler.end_headers()
        handler.wfile.write(body)

    def __enter__(self) -> FakeModelOpsServer:
        self._thread = threading.Thread(target=self.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.shutdown()
        self.server_close()
        assert self._thread is not None
        self._thread.join(timeout=5)
