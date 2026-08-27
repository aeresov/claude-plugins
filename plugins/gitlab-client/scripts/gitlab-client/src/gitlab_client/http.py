# SPDX-License-Identifier: AGPL-3.0-only
"""Thin urllib client: auth, encoding, pagination, redirects, error mapping, write policy.

Everything network-related lives here so `cli.py` and the helpers stay testable with a
stub opener (see tests/conftest.py).
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

from . import __version__
from .errors import ConfigError, GlError, HttpError, PolicyError

JSON_TIMEOUT = 30
DOWNLOAD_TIMEOUT = 300
MAX_RETRY_AFTER = 30
PER_PAGE = 100
CHUNK = 1 << 20
AUTH_HEADERS = ("Private-token", "Authorization")  # urllib capitalises header names this way
LINK_NEXT = re.compile(r'<([^>]+)>;\s*rel="next"')
KEYSET_PATHS = re.compile(r"/repository/tree/?$")

# Hints appended to error messages by status code (spec §8).
_HINTS = {
    401: " — token rejected; check token_cmd output and the token's expiry (/gitlab-client:doctor)",
    403: " — forbidden: role too low (Developer to retry/cancel/play/trigger, Reporter to read logs), a protected branch, or a missing token scope",
    404: " — GitLab returns 404 for both missing and not-visible resources; check the project path and the token's access",
    429: " — rate limited (already retried once after Retry-After)",
}


def _scalar(value: Any) -> str:
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def build_query(params: Mapping[str, Any]) -> str:
    """Encode a params mapping: bools → true/false, lists → key[]=v repeated, None skipped."""
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        if isinstance(value, (list, tuple)):
            pairs.extend((f"{key}[]", _scalar(v)) for v in value)
        elif value is not None:
            pairs.append((key, _scalar(value)))
    return urllib.parse.urlencode(pairs)


def describe_error(status: int, body: bytes) -> str:
    """Turn an error body (JSON `message`/`error` shapes, or plain text) into one line."""
    text = body.decode("utf-8", errors="replace").strip()
    data: Any = None
    if text:
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
    if isinstance(data, dict):
        if data.get("error") == "insufficient_scope":
            return f"token lacks the '{data.get('scope', '?')}' scope ({data.get('error_description', '')})"
        msg = data.get("message", data.get("error"))
        if msg is None:
            msg = text
        elif not isinstance(msg, str):
            msg = json.dumps(msg)
    else:
        msg = text[:200] or "(empty body)"
    return msg + _HINTS.get(status, "")


@dataclass
class Response:
    status: int
    headers: dict[str, str]  # lower-cased keys
    body: bytes
    url: str
    bytes_written: int = 0

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8")) if self.body.strip() else None

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower()) or default


class AuthStrippingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but drop auth headers when the target host differs from the API host.

    GitLab 302s artifact/trace downloads to pre-signed object-storage URLs; the token must
    not travel there.
    """

    def __init__(self, api_host: str):
        super().__init__()
        self.api_host = api_host.lower()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).netloc.lower() != self.api_host:
            for name in AUTH_HEADERS:
                new.remove_header(name)
        return new


def _retry_after(headers: Any) -> float:
    try:
        return float(headers.get("Retry-After", "1"))
    except (TypeError, ValueError, AttributeError):
        return 1.0


class Client:
    def __init__(self, base_url: str, token: str, *, opener: Any = None, sleep: Callable[[float], None] = time.sleep):
        self.base_url = base_url.rstrip("/")
        self.api = self.base_url + "/api/v4"
        self.host = urllib.parse.urlsplit(self.base_url).netloc
        self._token = token
        self._sleep = sleep
        self._opener = opener or urllib.request.build_opener(AuthStrippingRedirectHandler(self.host))

    def url_for(self, path: str, query: Mapping[str, Any] | None = None) -> str:
        url = self.api + (path if path.startswith("/") else "/" + path)
        qs = build_query(query) if query else ""
        if qs:
            url += ("&" if "?" in url else "?") + qs
        return url

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        json_body: Any = None,
        accept_json: bool = True,
        timeout: float | None = None,
        stream_to: Any = None,
        absolute_url: str | None = None,
    ) -> Response:
        method = method.upper()
        url = absolute_url or self.url_for(path, query)
        headers = {"User-Agent": f"gitlab-client/{__version__} (claude-code plugin)"}
        if accept_json:
            headers["Accept"] = "application/json"
        data = None
        if json_body is not None:
            data = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        req.add_header("PRIVATE-TOKEN", self._token)
        timeout = timeout or (DOWNLOAD_TIMEOUT if stream_to is not None else JSON_TIMEOUT)

        for attempt in (1, 2):
            try:
                with self._opener.open(req, timeout=timeout) as resp:
                    hdrs = {k.lower(): v for k, v in resp.headers.items()}
                    if stream_to is None:
                        return Response(resp.status, hdrs, resp.read(), url)
                    written = 0
                    while chunk := resp.read(CHUNK):
                        stream_to.write(chunk)
                        written += len(chunk)
                    return Response(resp.status, hdrs, b"", url, bytes_written=written)
            except urllib.error.HTTPError as e:
                body = e.fp.read() if e.fp is not None else b""
                if e.code == 429 and attempt == 1:
                    self._sleep(min(_retry_after(e.headers), MAX_RETRY_AFTER))
                    continue
                raise HttpError(e.code, method, url, describe_error(e.code, body)) from None
            except urllib.error.URLError as e:
                reason = e.reason
                raise GlError(f"{method} {url}: {type(reason).__name__}: {reason}") from None
            except OSError as e:  # socket timeouts and friends raised directly
                raise GlError(f"{method} {url}: {type(e).__name__}: {e}") from None
        raise AssertionError("unreachable")
