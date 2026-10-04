"""Test-only in-memory ModelOps API fake (ticket-060a): a real
``ThreadingHTTPServer`` on ``127.0.0.1:0``, started in a daemon thread
by its own context manager, so ``modelops_api.ModelOpsClient`` is
exercised against real localhost HTTP, real timeouts, and real
``urllib`` error handling -- not a mock. It serves ``workflows`` and
``tasks`` from two in-memory lists, records every request's method,
path, headers and body, and can be scripted to answer a given method
and path with a canned status, body and extra headers (for error,
redirect and redaction tests).

Ticket-063a adds the stateful write endpoints, with the ModelOps
behaviors the apply script must survive:

* ``PUT /api/Task/{id}`` overwrites ``createdBy`` with the request value
  (an omitted ``createdBy`` becomes ``null``); ``PUT /api/Workflow/{id}``
  keeps it. Neither stores ``timeout``, which GET never returns.
* ``POST /api/Task`` and ``POST /api/Workflow`` assign a new id and
  answer ``201`` with an empty body, so a client cannot learn the id.
* A workflow id in ``executing`` answers its PUT with 422 and the
  ModelOps message.
* ``hook()`` runs a callback that may mutate the stored documents at a
  chosen request, before or after it is handled.

PATCH and DELETE stay recorded and answered ``405``. 063 and 063a import
this fake from here and never redefine it.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import TracebackType
from typing import Any

_EXECUTING_MESSAGE = "Erro ao salvar fluxo. O fluxo está em execução."
_Reply = tuple[int, bytes, tuple[tuple[str, str], ...]]


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes = b""


@dataclass(frozen=True)
class _ScriptedResponse:
    status: int
    body: bytes
    headers: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class _Hook:
    method: str
    path: str
    action: Callable[[FakeModelOpsServer], None]
    after: bool


class _Handler(BaseHTTPRequestHandler):
    server: FakeModelOpsServer

    def log_message(self, format: str, *args: Any) -> None:
        pass

    def _handle(self) -> None:
        self.server.handle(self)

    do_GET = do_PUT = do_POST = do_PATCH = do_DELETE = _handle


class FakeModelOpsServer(ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.workflows: list[dict[str, Any]] = []
        self.tasks: list[dict[str, Any]] = []
        self.executing: set[str] = set()
        self.requests: list[RecordedRequest] = []
        self._lock = threading.Lock()
        self._scripts: dict[tuple[str, str], _ScriptedResponse] = {}
        self._hooks: list[_Hook] = []
        self._created = 0
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
        method: str = "GET",
    ) -> None:
        self._scripts[(method, path)] = _ScriptedResponse(
            status, body, tuple((headers or {}).items())
        )

    def hook(
        self,
        method: str,
        path: str,
        action: Callable[[FakeModelOpsServer], None],
        *,
        after: bool = False,
    ) -> None:
        """Run ``action`` once, on the first matching request: before the
        request is handled, or (``after``) once it has been handled but
        before its reply is sent."""
        self._hooks.append(_Hook(method, path, action, after))

    def _fire(self, method: str, path: str, *, after: bool) -> None:
        with self._lock:
            due = [
                hook
                for hook in self._hooks
                if (hook.method, hook.path, hook.after) == (method, path, after)
            ]
            for hook in due:
                self._hooks.remove(hook)
                hook.action(self)

    def handle(self, handler: _Handler) -> None:
        method, path = handler.command, handler.path
        body = handler.rfile.read(int(handler.headers.get("Content-Length", 0)))
        with self._lock:
            self.requests.append(
                RecordedRequest(
                    method=method,
                    path=path,
                    headers=dict(handler.headers.items()),
                    body=body,
                )
            )
        self._fire(method, path, after=False)
        status, payload, headers = self._reply(method, path, body)
        self._fire(method, path, after=True)
        self._respond(handler, status, payload, headers)

    def _reply(self, method: str, path: str, body: bytes) -> _Reply:
        with self._lock:
            scripted = self._scripts.get((method, path))
            if scripted is not None:
                return scripted.status, scripted.body, scripted.headers
            if method == "GET":
                return self._get(path)
            if method == "PUT":
                return self._put(path, body)
            if method == "POST":
                return self._post(path, body)
        return 405, b'{"error":"method not allowed"}', ()

    def _get(self, path: str) -> _Reply:
        payload = self._payload_for(path)
        if payload is None:
            return 404, b'{"error":"not found"}', ()
        return 200, json.dumps(payload).encode("utf-8"), ()

    def _put(self, path: str, body: bytes) -> _Reply:
        kind, _, doc_id = path.removeprefix("/api/").partition("/")
        collection = self._collections().get(kind)
        doc = None if collection is None else self._find(collection, doc_id)
        if doc is None:
            return 404, b'{"error":"not found"}', ()
        if kind == "Workflow" and doc_id in self.executing:
            message = json.dumps(
                {"message": _EXECUTING_MESSAGE}, ensure_ascii=False
            )
            return 422, message.encode("utf-8"), ()
        sent = json.loads(body)
        created_by = (
            sent.get("createdBy") if kind == "Task" else doc.get("createdBy")
        )
        doc.update({k: v for k, v in sent.items() if k != "timeout"})
        doc["createdBy"] = created_by
        doc["lastChangeDate"] = "2026-10-03T12:00:00Z"
        return 204, b"", ()

    def _post(self, path: str, body: bytes) -> _Reply:
        kind = path.removeprefix("/api/")
        collection = self._collections().get(kind)
        if collection is None:
            return 404, b'{"error":"not found"}', ()
        self._created += 1
        sent = json.loads(body)
        collection.append(
            {
                **{k: v for k, v in sent.items() if k != "timeout"},
                "_id": f"new-{kind.lower()}-{self._created}",
            }
        )
        return 201, b"", ()

    def _collections(self) -> dict[str, list[dict[str, Any]]]:
        return {"Task": self.tasks, "Workflow": self.workflows}

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
