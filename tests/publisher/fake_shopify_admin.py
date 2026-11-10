"""An in-memory Shopify Admin for the publisher tests: token endpoint, GraphQL, theme files.

Tests talk to it through ``fake.opener`` (``open(request, timeout=)``, the `urllib.request` shape,
raising ``HTTPError`` for non-2xx) so no socket is ever opened. Content queries (the export's)
are answered by a ``backend`` runner ``(query, variables) -> data``; ``currentAppInstallation``
and ``theme(id).files`` are answered here. Every request is recorded; failures are scripted
with ``fail_next``, ``throttle_next`` and ``errors_next``.
"""
from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import urllib.error
import urllib.parse
from dataclasses import dataclass
from typing import Any, Callable, Optional

from fake_clock import FakeClock

READ_SCOPES = ("read_content", "read_products", "read_online_store_navigation", "read_files",
               "read_themes", "read_metaobjects")
SHOP = "rosart-mhfmyuxq.myshopify.com"


@dataclass
class RecordedRequest:
    kind: str                      # "token" or "graphql"
    url: str
    method: str
    headers: dict
    body: str
    token: Optional[str] = None    # X-Shopify-Access-Token of a GraphQL request
    query: str = ""
    variables: Optional[dict] = None


class _Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body
        self.headers = {"Content-Type": "application/json"}

    def read(self) -> bytes:
        return self._body

    def getcode(self) -> int:
        return self.status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Opener:
    def __init__(self, fake: "FakeShopifyAdmin") -> None:
        self._fake = fake

    def open(self, request, data=None, timeout=None):
        return self._fake.handle(request)


class FakeShopifyAdmin:
    def __init__(self, clock: Optional[FakeClock] = None, shop: str = SHOP, api_version: str = "2026-10",
                 backend: Optional[Callable[[str, dict], dict]] = None, url_host: str = "cdn.shopify.com") -> None:
        self.clock = clock or FakeClock()
        self.shop = shop
        self.api_version = api_version
        self.backend = backend
        self.url_host = url_host
        self.client_id = "fake-client-id"
        self.client_secret = "fake-client-secret"
        self.grant_scope = ",".join(READ_SCOPES)
        self.grant_error: Optional[str] = None
        self.expires_in = 86399
        self.stored_token: Optional[str] = None
        self.stored_scopes: list[str] = list(READ_SCOPES)
        self.requests: list[RecordedRequest] = []
        self.fetched_urls: list[str] = []
        self.opener = _Opener(self)
        self._issued = 0
        self.issued_tokens: list[str] = []
        self._tokens: dict[str, tuple[float, list[str]]] = {}
        self._queue: list[Callable[[], _Response]] = []
        self._themes: dict[str, dict] = {}
        self._url_bodies: dict[str, bytes] = {}

    # ------------------------------------------------------------ inspection
    @property
    def token_requests(self) -> list[RecordedRequest]:
        return [r for r in self.requests if r.kind == "token"]

    @property
    def graphql_requests(self) -> list[RecordedRequest]:
        return [r for r in self.requests if r.kind == "graphql"]

    # ------------------------------------------------------------ scripting
    def revoke_tokens(self) -> None:
        self._tokens.clear()
        self.stored_token = None

    def fail_next(self, status: int, times: int = 1) -> None:
        for _ in range(times):
            self._queue.append(lambda s=status: self._http_error(s, {"errors": f"fake {s}"}))

    def throttle_next(self, requested: float = 100, available: float = 0, restore_rate: float = 50,
                      times: int = 1) -> None:
        for _ in range(times):
            self._queue.append(lambda: _Response(200, self._json({
                "errors": [{"message": "Throttled", "extensions": {"code": "THROTTLED"}}],
                "extensions": {"cost": {"requestedQueryCost": requested, "actualQueryCost": None,
                                        "throttleStatus": {"maximumAvailable": 1000.0,
                                                           "currentlyAvailable": available,
                                                           "restoreRate": float(restore_rate)}}}})))

    def errors_next(self, messages: list[str], times: int = 1) -> None:
        for _ in range(times):
            self._queue.append(lambda: _Response(200, self._json({"errors": [{"message": m} for m in messages]})))

    def add_theme(self, theme_gid: str, files: Optional[dict[str, bytes]] = None, role: str = "MAIN",
                  name: str = "Rosart", body_types: Optional[dict[str, str]] = None, page_size: int = 250,
                  overrides: Optional[dict[str, dict]] = None, user_errors: Optional[list[dict]] = None) -> None:
        self._themes[theme_gid] = {"role": role, "name": name, "files": dict(files or {}),
                                   "body_types": dict(body_types or {}), "page_size": page_size,
                                   "overrides": dict(overrides or {}), "user_errors": list(user_errors or [])}

    def fetch(self, url: str) -> bytes:
        """The fake CDN: what a Url body points at."""
        self.fetched_urls.append(url)
        return self._url_bodies[url]

    # ------------------------------------------------------------ the opener
    def handle(self, request) -> _Response:
        url = request.full_url
        body = (request.data or b"").decode("utf-8")
        headers = {k.lower(): v for k, v in request.header_items()}
        parsed = urllib.parse.urlparse(url)
        if parsed.netloc != self.shop:
            raise AssertionError(f"fake Shopify Admin got a request for {parsed.netloc}")
        if parsed.path == "/admin/oauth/access_token":
            self.requests.append(RecordedRequest("token", url, request.get_method(), headers, body))
            return self._token(body)
        if parsed.path == f"/admin/api/{self.api_version}/graphql.json":
            payload = json.loads(body)
            token = headers.get("x-shopify-access-token") or None
            self.requests.append(RecordedRequest("graphql", url, request.get_method(), headers, body, token,
                                                 payload.get("query", ""), payload.get("variables") or {}))
            return self._graphql(token, payload.get("query", ""), payload.get("variables") or {})
        return self._http_error(404, {"errors": "Not Found"}, url=url)

    # ------------------------------------------------------------ internals
    @staticmethod
    def _json(value: Any) -> bytes:
        return json.dumps(value).encode("utf-8")

    def _http_error(self, status: int, body: dict, url: str = "") -> _Response:
        raise urllib.error.HTTPError(url, status, f"fake {status}", {}, io.BytesIO(self._json(body)))

    def _token(self, body: str) -> _Response:
        form = urllib.parse.parse_qs(body)
        if self.grant_error:
            return self._http_error(400, {"error": self.grant_error,
                                          "error_description": "fake grant refused"})
        if (form.get("grant_type") != ["client_credentials"] or form.get("client_id") != [self.client_id]
                or form.get("client_secret") != [self.client_secret]):
            return self._http_error(400, {"error": "invalid_client", "error_description": "fake"})
        self._issued += 1
        token = f"shpat_fake_{self._issued:04d}"
        self.issued_tokens.append(token)
        self._tokens[token] = (self.clock.mono() + self.expires_in, self.grant_scope.split(","))
        return _Response(200, self._json({"access_token": token, "scope": self.grant_scope,
                                          "expires_in": self.expires_in}))

    def _scopes_of(self, token: Optional[str]) -> Optional[list[str]]:
        if token is not None and token == self.stored_token:
            return self.stored_scopes
        entry = self._tokens.get(token or "")
        if entry and self.clock.mono() < entry[0]:
            return entry[1]
        return None

    def _cost(self) -> dict:
        return {"cost": {"requestedQueryCost": 10, "actualQueryCost": 5,
                         "throttleStatus": {"maximumAvailable": 1000.0, "currentlyAvailable": 995.0,
                                            "restoreRate": 50.0}}}

    def _graphql(self, token: Optional[str], query: str, variables: dict) -> _Response:
        if self._queue:
            return self._queue.pop(0)()
        scopes = self._scopes_of(token)
        if scopes is None:
            return self._http_error(401, {"errors": "[API] Invalid API key or access token"})
        if "currentAppInstallation" in query:
            data: dict = {"currentAppInstallation": {"accessScopes": [{"handle": s} for s in scopes]}}
        elif re.search(r"\btheme\s*\(", query):
            data = {"theme": self._theme(variables)}
        elif self.backend is not None:
            data = self.backend(query, variables)
        else:
            raise AssertionError("fake Shopify Admin has no backend for this query")
        return _Response(200, self._json({"data": data, "extensions": self._cost()}))

    def _theme(self, variables: dict) -> Optional[dict]:
        theme = self._themes.get(variables.get("id", ""))
        if theme is None:
            return None
        names = sorted(theme["files"])
        start = int(variables.get("after") or 0)
        end = start + theme["page_size"]
        nodes = [self._file_node(theme, name) for name in names[start:end]]
        more = end < len(names)
        return {"id": variables["id"], "name": theme["name"], "role": theme["role"],
                "files": {"nodes": nodes, "pageInfo": {"hasNextPage": more, "endCursor": str(end) if more else None},
                          "userErrors": theme["user_errors"]}}

    def _file_node(self, theme: dict, name: str) -> dict:
        data = theme["files"][name]
        kind = theme["body_types"].get(name)
        if kind is None:
            try:
                data.decode("utf-8")
                kind = "text"
            except UnicodeDecodeError:
                kind = "base64"
        if kind == "text":
            body = {"content": data.decode("utf-8")}
        elif kind == "base64":
            body = {"contentBase64": base64.b64encode(data).decode("ascii")}
        else:
            url = f"https://{self.url_host}/s/files/1/0/themes/{name}?v=1"
            self._url_bodies[url] = data
            body = {"url": url}
        node = {"filename": name, "size": str(len(data)), "checksumMd5": hashlib.md5(data).hexdigest(),
                "contentType": "application/octet-stream", "body": body}
        node.update(theme["overrides"].get(name, {}))
        return node
