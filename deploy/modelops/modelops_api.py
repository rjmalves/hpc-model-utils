"""deploy.modelops.modelops_api: the ModelOps API client (ADR-033, R98).
``get_json`` reads; ``put_json`` and ``post_json`` (ticket-063a) write
through the same opener, timeout and redaction. ``MODELOPS_TOKEN`` is
read from the environment only and is redacted, with any AWS credential,
out of every exception message this client raises -- ``redact()`` is the
one masking function, applied to a full response body before it is cut
to ``_MAX_BODY_CHARS``; TLS verification always runs through
``ssl.create_default_context()`` with no flag or variable to turn it off.

Every request goes through a dedicated ``OpenerDirector`` (built fresh
per call in ``_build_opener()``) rather than ``urlopen()``'s default,
global one: the default opener's ``HTTPRedirectHandler`` follows any
``3xx`` to any ``Location`` -- any scheme, any host -- replaying the
``Authorization`` header onto it, and its default ``ProxyHandler``
reads ``HTTP_PROXY``/``HTTPS_PROXY`` and would route the request (and
the token) through whatever the environment names. ``_NoRedirectHandler``
turns every ``3xx`` into a ``ModelOpsApiError`` before any request
reaches a redirect target, and ``ProxyHandler({})`` disables all
environment proxying, so the bearer token can only ever reach the
configured ``MODELOPS_URL`` host.
"""

from __future__ import annotations

import json
import re
import ssl
import urllib.error
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, NoReturn
from urllib.parse import urlsplit

_TIMEOUT_S = 30.0
_MAX_BODY_CHARS = 200
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_BEARER_RE = re.compile(r"Bearer\s+\S+")
_AWS_RE = re.compile(r"AKIA[0-9A-Z]{16}|(?i:aws_secret_access_key=\S+)")


class ConfigError(Exception):
    pass


class ModelOpsApiError(Exception):
    pass


def redact(text: str, token: str) -> str:
    result = _BEARER_RE.sub("<redacted>", text)
    if token:
        result = result.replace(token, "<redacted>")
    return _AWS_RE.sub("<redacted>", result)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> NoReturn:
        raise urllib.error.URLError(f"redirect to HTTP {code} refused")


def _build_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        _NoRedirectHandler(),
        urllib.request.ProxyHandler({}),
    )


@dataclass(frozen=True)
class ModelOpsClient:
    base_url: str
    token: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> ModelOpsClient:
        url = env.get("MODELOPS_URL", "")
        token = env.get("MODELOPS_TOKEN", "")
        if not url:
            raise ConfigError("MODELOPS_URL is not set")
        if not token:
            raise ConfigError("MODELOPS_TOKEN is not set")
        parsed = urlsplit(url)
        # Never echo the raw URL below: userinfo, a query string or a
        # fragment could carry a credential pasted in by mistake. Only
        # the scheme and host -- never secret -- are safe to report.
        scheme_host = f"{parsed.scheme}://{parsed.hostname or '?'}"
        if parsed.username is not None or parsed.password is not None:
            raise ConfigError(
                f"MODELOPS_URL must not contain userinfo ({scheme_host})"
            )
        if parsed.query:
            raise ConfigError(
                f"MODELOPS_URL must not contain a query string ({scheme_host})"
            )
        if parsed.fragment:
            raise ConfigError(
                f"MODELOPS_URL must not contain a fragment ({scheme_host})"
            )
        host = parsed.hostname or ""
        if parsed.scheme == "https" or (
            parsed.scheme == "http" and host in _LOCAL_HOSTS
        ):
            return cls(base_url=url.rstrip("/"), token=token)
        raise ConfigError(
            "MODELOPS_URL must use https:// (http:// is allowed only for "
            f"localhost, 127.0.0.1 or ::1): {scheme_host}"
        )

    def _send(self, method: str, path: str, body: Any = None) -> bytes:
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/json",
        }
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with _build_opener().open(request, timeout=_TIMEOUT_S) as response:
                payload: bytes = response.read()
        except urllib.error.HTTPError as err:
            text = redact(
                err.read().decode("utf-8", errors="replace"), self.token
            )[:_MAX_BODY_CHARS]
            raise ModelOpsApiError(
                redact(f"{method} {path}: HTTP {err.code}: {text}", self.token)
            ) from err
        except (urllib.error.URLError, ssl.SSLError, TimeoutError) as err:
            raise ModelOpsApiError(
                redact(f"{method} {path}: {err}", self.token)
            ) from err
        return payload

    def get_json(self, path: str) -> Any:
        raw = self._send("GET", path)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as err:
            raise ModelOpsApiError(
                redact(f"GET {path}: invalid JSON response: {err}", self.token)
            ) from err

    def put_json(self, path: str, body: Any) -> None:
        self._send("PUT", path, body)

    def post_json(self, path: str, body: Any) -> None:
        self._send("POST", path, body)
