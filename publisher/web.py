"""The publisher's HTTP server, router and request rules (spec 006 T046; contracts/http-routes.md).

Standard library only (``http.server``). The service routes the ``PUBLISHER_BASE_PATH`` prefix itself:
nginx passes ``/rosart/...`` through unchanged. Rules applied to every request, in this order:

1. a method other than GET, HEAD or POST is 405;
2. a path outside the prefix is a plain 404, and the bare prefix is a 301 to ``prefix/``;
3. ``/healthz``, ``/status.json`` and ``/static/*`` ignore identity (GET and HEAD only);
4. every other route needs ``Remote-User`` (403 without it);
5. POST needs ``PUBLISHER_GROUP`` in ``Remote-Groups`` (403 without it);
6. the route table decides: 404 for an unknown path, 405 for a known path with another method.

Handlers receive a :class:`Request` and return a :class:`Response`; a handler's exception becomes a
plain 500 page and never shows a stack trace.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

from publisher.pages import Pages, esc

log = logging.getLogger("rosart.web")

MAX_BODY = 4096
DRAIN_LIMIT = 65536
ALLOWED_METHODS = "GET, HEAD, POST"
STATIC_DIR = Path(__file__).resolve().parent / "static"
CSP = ("default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; "
       "frame-ancestors 'none'; base-uri 'none'")

NO_IDENTITY = "No sign-in identity reached the publisher; contact the owner"
NO_ACCESS = "You do not have access to publishing; ask the owner"


@dataclass
class Request:
    method: str
    path: str                       # without the prefix, always starting with "/"
    query: dict[str, str]
    form: dict[str, str]
    headers: Any
    user: str = ""
    name: str = ""
    email: str = ""
    groups: list[str] = field(default_factory=list)


@dataclass
class Response:
    status: int = 200
    body: Any = ""                  # str or bytes
    content_type: str = "text/html; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)

    @classmethod
    def json(cls, data: Any, status: int = 200) -> "Response":
        return cls(status, json.dumps(data), "application/json")


Handler = Callable[[Request], Response]


def _utf8(value: str) -> str:
    """Headers arrive decoded as Latin-1; Authelia sends UTF-8 bytes, so undo that."""
    try:
        return value.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


class App:
    def __init__(self, config: Any, store: Any, clock: Any, code_commit: str = "unknown") -> None:
        self.config = config
        self.store = store
        self.clock = clock
        self.code_commit = code_commit
        self.pages = Pages(config.base_path)
        self._routes: dict[tuple[str, str], Handler] = {}
        self.add_route("GET", "/", self._home)
        self.add_route("GET", "/static/app.css", self._stylesheet)

    def add_route(self, method: str, path: str, handler: Handler) -> None:
        self._routes[(method, path)] = handler

    # --- dispatch -------------------------------------------------------------------------

    def message(self, status: int, message: str, headers: Optional[dict[str, str]] = None) -> Response:
        return Response(status, self.pages.message_page(status, message), headers=headers or {})

    def dispatch(self, method: str, target: str, headers: Any, body: bytes = b"") -> Response:
        try:
            return self._finish(self._dispatch(method, target, headers, body))
        except Exception:  # noqa: BLE001 - never leak a trace to the page
            log.exception("unhandled error serving %s %s", method, target.partition("?")[0])
            return self._finish(self.message(500, "Something went wrong; ask the owner (the job log has the detail)"))

    def _dispatch(self, method: str, target: str, headers: Any, body: bytes) -> Response:
        if method not in ("GET", "HEAD", "POST"):
            return self.message(405, "That method is not allowed here", {"Allow": ALLOWED_METHODS})
        raw_path, _, raw_query = target.partition("?")
        base = self.config.base_path
        if raw_path == base:
            return Response(301, self.pages.message_page(301, "Moved", "Moved"), headers={"Location": self.pages.url("/")})
        if not raw_path.startswith(base + "/"):
            return self.message(404, "There is nothing at this address")
        path = raw_path[len(base):]
        lookup = "GET" if method == "HEAD" else method

        if path in ("/healthz", "/status.json") or path.startswith("/static/"):
            if lookup != "GET":
                return self.message(405, "That method is not allowed here", {"Allow": ALLOWED_METHODS})
            return self._call(self._request(lookup, path, raw_query, headers, body, ""))

        user = _utf8((headers.get("Remote-User") or "").strip())
        if not user:
            return self.message(403, NO_IDENTITY)
        request = self._request(lookup, path, raw_query, headers, body, user)
        if lookup == "POST" and self.config.group not in request.groups:
            return self.message(403, NO_ACCESS)
        return self._call(request)

    def _request(self, method: str, path: str, raw_query: str, headers: Any, body: bytes, user: str) -> Request:
        name = _utf8((headers.get("Remote-Name") or "").strip()) or user
        email = _utf8((headers.get("Remote-Email") or "").strip()) or f"{user}@rosart-publisher.invalid"
        groups = [g.strip() for g in (headers.get("Remote-Groups") or "").split(",") if g.strip()]
        return Request(method, path, _first(urllib.parse.parse_qs(raw_query, keep_blank_values=True)),
                       _first(urllib.parse.parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)),
                       headers, user, name, email, groups)

    def _call(self, request: Request) -> Response:
        handler = self._routes.get((request.method, request.path))
        if handler is None:
            if any(path == request.path for (_, path) in self._routes):
                return self.message(405, "That method is not allowed here", {"Allow": ALLOWED_METHODS})
            return self.message(404, "There is nothing at this address")
        response = handler(request)
        if not isinstance(response, Response):
            raise TypeError("a handler must return a Response")
        return response

    def _finish(self, response: Response) -> Response:
        """Headers every response carries (http-routes.md 'Security headers' and 'Caching')."""
        headers = dict(response.headers)
        headers.setdefault("Cache-Control", "no-store")
        if response.content_type.startswith("text/html"):
            headers["Content-Security-Policy"] = CSP
            headers["Referrer-Policy"] = "same-origin"
        return Response(response.status, response.body, response.content_type, headers)

    # --- the pages of this phase ----------------------------------------------------------

    def _home(self, request: Request) -> Response:
        body = f"<p>Signed in as {esc(request.name)}.</p>"
        return Response(200, self.pages.layout("Rosart website publisher", body))

    def _stylesheet(self, request: Request) -> Response:
        data = (STATIC_DIR / "app.css").read_bytes()
        return Response(200, data, "text/css; charset=utf-8", {"Cache-Control": "max-age=3600"})


def _first(parsed: dict[str, list[str]]) -> dict[str, str]:
    return {key: values[0] for key, values in parsed.items()}


# --- the HTTP layer ---------------------------------------------------------------------------

class PublisherServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], app: App) -> None:
        self.app = app
        super().__init__(address, _Handler)


class _Handler(BaseHTTPRequestHandler):
    server_version = "rosart-publisher"
    sys_version = ""
    protocol_version = "HTTP/1.1"

    def __getattr__(self, name: str):
        if name.startswith("do_"):          # every method reaches the app, which answers 405 itself
            return self._serve
        raise AttributeError(name)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        log.debug("%s - %s", self.address_string(), format % args)

    def _serve(self) -> None:
        app: App = self.server.app  # type: ignore[attr-defined]
        refusal = self._read_body()
        if isinstance(refusal, Response):
            self._send(app._finish(refusal), head=False)
            return
        response = app.dispatch(self.command, self.path, self.headers, refusal)
        self._send(response, head=self.command == "HEAD")

    def _read_body(self) -> Any:
        """The request body, or a refusal Response (and the connection is then closed)."""
        app: App = self.server.app  # type: ignore[attr-defined]
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            self.close_connection = True
            return app.message(411, "A request body must declare its length")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = -1
        if length < 0:
            self.close_connection = True
            return app.message(400, "The request is not valid")
        if self.command != "POST":
            if length:
                self.close_connection = True      # an unread body would corrupt a kept-alive connection
            return b""
        if length > MAX_BODY:
            self.rfile.read(min(length, DRAIN_LIMIT))      # let the client finish sending before we answer
            self.close_connection = True
            return app.message(413, "That request is too large")
        data = self.rfile.read(length) if length else b""
        if data and not (self.headers.get("Content-Type") or "").lower().startswith("application/x-www-form-urlencoded"):
            return app.message(415, "Send a form, not another kind of body")
        return data

    def _send(self, response: Response, head: bool) -> None:
        body = response.body.encode("utf-8") if isinstance(response.body, str) else bytes(response.body)
        self.send_response(response.status)
        self.send_header("Content-Type", response.content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in response.headers.items():
            self.send_header(name, value)
        if self.close_connection:
            self.send_header("Connection", "close")
        self.end_headers()
        if not head:
            self.wfile.write(body)


def make_server(app: App, bind: str) -> PublisherServer:
    """A server bound to ``host:port`` (``127.0.0.1:0`` picks a free port; read ``server_address``)."""
    host, _, port = bind.rpartition(":")
    return PublisherServer((host or "0.0.0.0", int(port)), app)
