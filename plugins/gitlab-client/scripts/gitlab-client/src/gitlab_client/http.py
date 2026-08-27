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
    bytes_written: int = 0

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8")) if self.body.strip() else None

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower()) or default


class AuthStrippingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but drop auth headers unless the target is the API origin (scheme + host).

    GitLab 302s artifact/trace downloads to pre-signed object-storage URLs; the token must
    not travel there — nor over a same-host https→http downgrade.
    """

    def __init__(self, api_host: str, scheme: str = "https"):
        super().__init__()
        self.api_host = api_host.lower()
        self.scheme = scheme.lower()

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        target = urllib.parse.urlsplit(newurl)
        if new is not None and (target.netloc.lower(), target.scheme.lower()) != (self.api_host, self.scheme):
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
        origin = urllib.parse.urlsplit(self.base_url)
        self.host = origin.netloc
        self.scheme = origin.scheme
        self._token = token
        self._sleep = sleep
        self._opener = opener or urllib.request.build_opener(AuthStrippingRedirectHandler(self.host, self.scheme))

    def url_for(self, path: str, query: Mapping[str, Any] | None = None) -> str:
        url = self.api + (path if path.startswith("/") else "/" + path)
        qs = build_query(query) if query else ""
        if qs:
            url += ("&" if "?" in url else "?") + qs
        return url

    def same_origin(self, url: str) -> str:
        """Re-anchor a server-supplied URL (a Link header) on our scheme + host.

        The token is attached to every request, so a proxy that rewrites Link URLs to http://
        or to another host must not be able to redirect it.
        """
        parts = urllib.parse.urlsplit(url)
        return urllib.parse.urlunsplit((self.scheme, self.host, parts.path, parts.query, ""))

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        json_body: Any = None,
        accept_json: bool = True,
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
        timeout = DOWNLOAD_TIMEOUT if stream_to is not None else JSON_TIMEOUT

        for attempt in (1, 2):
            try:
                with self._opener.open(req, timeout=timeout) as resp:
                    hdrs = {k.lower(): v for k, v in resp.headers.items()}
                    if stream_to is None:
                        return Response(resp.status, hdrs, resp.read())
                    written = 0
                    while chunk := resp.read(CHUNK):
                        stream_to.write(chunk)
                        written += len(chunk)
                    return Response(resp.status, hdrs, b"", bytes_written=written)
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


# ---- parameters, project placeholder, write policy ------------------------------------------

_PARAM = re.compile(r"^([A-Za-z0-9_.\-\[\]]+)(:=|=)(.*)$", re.S)


def parse_params(items: Iterable[str]) -> dict[str, Any]:
    """`key=value` → str; `key:=json` → parsed JSON; `key[]=value` → list (repeatable)."""
    out: dict[str, Any] = {}
    for item in items:
        m = _PARAM.match(item)
        if not m:
            raise ConfigError(f"parameter must be key=value, key:=json or key[]=value: {item!r}")
        key, op, raw = m.groups()
        if op == ":=":
            try:
                value: Any = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ConfigError(f"bad JSON for {key}: {e}") from None
        else:
            value = raw
        if key.endswith("[]"):
            out.setdefault(key[:-2], []).append(value)
        else:
            out[key] = value
    return out


def encode_path_segment(value: str) -> str:
    return urllib.parse.quote(value, safe="")


def substitute_project(path: str, project_path: str | None) -> str:
    if ":project" not in path:
        return path
    if not project_path:
        raise ConfigError("path uses :project but no project could be resolved")
    return path.replace(":project", encode_path_segment(project_path))


_P = r"/projects/[^/]+"
_MR = _P + r"/merge_requests/\d+"
_DISC = _MR + r"/discussions/[0-9a-fA-F]+"
WRITE_ALLOW = [
    re.compile(p)
    for p in (
        rf"^POST {_P}/merge_requests$",
        rf"^PUT {_MR}$",
        rf"^POST {_MR}/notes$",
        rf"^POST {_MR}/discussions$",
        rf"^POST {_DISC}/notes$",
        rf"^PUT {_DISC}$",
        rf"^POST {_P}/pipeline$",
        rf"^POST {_P}/pipelines/\d+/(retry|cancel)$",
        rf"^POST {_P}/jobs/\d+/(retry|cancel|play)$",
        rf"^POST {_P}/jobs/\d+/artifacts/keep$",
    )
]


# Anything outside printable ASCII, plus '#': urllib would send a different path than the one we
# matched (it truncates at '#'; whitespace/control characters break the request line).
_UNSAFE_PATH = re.compile(r"[^\x21-\x7e]|#")


def check_write_policy(method: str, path: str) -> None:
    """Refuse paths that wouldn't round-trip; GET then passes; POST/PUT must match WRITE_ALLOW."""
    method = method.upper()
    if _UNSAFE_PATH.search(path):
        raise PolicyError(
            f"refused by gitlab-client write policy: path must be URL-encoded printable ASCII without '#': {path!r}"
        )
    if any(urllib.parse.unquote(seg) in (".", "..") for seg in path.split("?", 1)[0].split("/")):
        raise PolicyError(f"refused by gitlab-client write policy: path contains a dot segment: {path!r}")
    if method == "GET":
        return
    if method not in ("POST", "PUT"):
        raise PolicyError(f"{method} is not supported by gl (only GET, POST, PUT)")
    key = f"{method} {path.split('?', 1)[0].rstrip('/')}"
    if not any(p.match(key) for p in WRITE_ALLOW):
        raise PolicyError(f"refused by gitlab-client write policy: {key} (see references/safety-perimeter.md)")


# ---- pagination and projection --------------------------------------------------------------


def _paginate(self: Client, path: str, query: Mapping[str, Any] | None, *, max_items: int = 1000,
              warn: Callable[[str], None] = lambda m: None) -> list[Any]:
    """Follow pagination and return one concatenated list.

    Offset endpoints: per_page=100, loop on the x-next-page header. /repository/tree is
    keyset-only on GitLab ≥ 15.0: pagination=keyset and follow the Link rel="next" URL.
    """
    q: dict[str, Any] = dict(query or {})
    q["per_page"] = PER_PAGE
    keyset = bool(KEYSET_PATHS.search(path.split("?", 1)[0]))
    if keyset:
        q["pagination"] = "keyset"
    else:
        q["page"] = 1
    items: list[Any] = []
    next_url: str | None = None
    while True:
        resp = self.request("GET", path, absolute_url=next_url) if next_url else self.request("GET", path, query=q)
        page = resp.json()
        if not isinstance(page, list):
            raise GlError(f"--all expects an array response, got {type(page).__name__}")
        items.extend(page)
        if keyset:
            m = LINK_NEXT.search(resp.header("link"))
            has_next = bool(m and page)
            next_url = self.same_origin(m.group(1)) if has_next else None
        else:
            nxt = resp.header("x-next-page")
            if nxt and page:
                has_next, next_url = True, None
                q["page"] = int(nxt)
            else:  # some proxies drop x-* headers; fall back to the Link header
                m = LINK_NEXT.search(resp.header("link"))
                has_next = bool(m and page)
                next_url = self.same_origin(m.group(1)) if has_next else None
        if len(items) >= max_items:
            if len(items) > max_items or has_next:
                warn(f"gl: warning: --all stopped at --max {max_items} items; more are available")
            return items[:max_items]
        if not has_next:
            return items


Client.paginate = _paginate  # type: ignore[attr-defined]


def project_fields(data: Any, fields: list[str]) -> Any:
    """Keep only the listed dotted paths of each object; missing → None."""

    def pick(obj: Any) -> Any:
        if not isinstance(obj, dict):
            return obj
        out: dict[str, Any] = {}
        for f in fields:
            cur: Any = obj
            for part in f.split("."):
                if isinstance(cur, dict):
                    cur = cur.get(part)
                elif isinstance(cur, list) and part.isdigit():
                    cur = cur[int(part)] if int(part) < len(cur) else None
                else:
                    cur = None
                if cur is None:
                    break
            out[f] = cur
        return out

    return [pick(x) for x in data] if isinstance(data, list) else pick(data)
