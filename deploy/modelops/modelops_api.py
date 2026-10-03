"""deploy.modelops.modelops_api: the read-only ModelOps API client
(ADR-033, R98). ``MODELOPS_TOKEN`` is read from the environment only
and is redacted out of every exception message this client raises;
TLS verification always runs through ``ssl.create_default_context()``
with no flag or variable to turn it off.

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


class ConfigError(Exception):
    pass


class ModelOpsApiError(Exception):
    pass


def redact(text: str, token: str) -> str:
    result = _BEARER_RE.sub("<redacted>", text)
    if token:
        result = result.replace(token, "<redacted>")
    return result


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

    def get_json(self, path: str) -> Any:
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
            },
        )
        try:
            with _build_opener().open(request, timeout=_TIMEOUT_S) as response:
                raw = response.read()
        except urllib.error.HTTPError as err:
            body = redact(
                err.read().decode("utf-8", errors="replace")[:_MAX_BODY_CHARS],
                self.token,
            )
            raise ModelOpsApiError(
                redact(f"GET {path}: HTTP {err.code}: {body}", self.token)
            ) from err
        except (urllib.error.URLError, ssl.SSLError, TimeoutError) as err:
            raise ModelOpsApiError(
                redact(f"GET {path}: {err}", self.token)
            ) from err
        try:
            return json.loads(raw)
        except json.JSONDecodeError as err:
            raise ModelOpsApiError(
                redact(f"GET {path}: invalid JSON response: {err}", self.token)
            ) from err
