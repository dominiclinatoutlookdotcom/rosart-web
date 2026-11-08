"""A real publisher server on 127.0.0.1:0 and a tiny client for it (spec 006 web tests).

``Harness`` builds the config, the job store and the ``App``, serves it from a thread and talks to it
over ``http.client`` without following redirects. Paths given to ``get`` and ``post`` are *without*
the ``PUBLISHER_BASE_PATH`` prefix; ``raw`` sends exactly what it is given. Identity headers default to
a signed-in publisher; ``user=None`` sends none. ``/probe`` (GET and POST) is a route that records the
requests that reached a handler.
"""
from __future__ import annotations

import http.client
import json
import re
import threading
import urllib.parse
from dataclasses import dataclass, field

from fake_clock import FakeClock

ORIGIN = "https://int.solagratia.com.au"
VALID = object()          # post(csrf=VALID): issue a good token for the posting user

ENV = {
    "ROSART_SHOPIFY_CLIENT_ID": "client-id-planted",
    "ROSART_SHOPIFY_CLIENT_SECRET": "client-secret-planted",
    "ROSART_AWS_ACCESS_KEY_ID": "AKIAPLANTEDKEY0000",
    "ROSART_AWS_SECRET_ACCESS_KEY": "aws-secret-planted-value",
    "SLACK_BOT_TOKEN": "xoxb-planted-slack",
    "SLACK_SYSTEM_CHANNEL_ID": "C0PLANTED",
}


@dataclass
class Reply:
    status: int
    headers: dict
    raw: bytes

    @property
    def body(self) -> str:
        return self.raw.decode("utf-8", errors="replace")

    def json(self):
        return json.loads(self.body)


@dataclass
class Harness:
    base: str
    config: object
    store: object
    app: object
    server: object
    clock: FakeClock
    port: int
    probes: list = field(default_factory=list)

    def raw(self, method, full_path, headers=None, body=None) -> Reply:
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request(method, full_path, body=body, headers=headers or {})
            response = conn.getresponse()
            return Reply(response.status, {k.lower(): v for k, v in response.getheaders()}, response.read())
        finally:
            conn.close()

    @staticmethod
    def identity(user="denise", name="Denise Hill", email="denise@example.org", groups="rosart-publishers"):
        if user is None:
            return {}
        return {"Remote-User": user, "Remote-Name": name, "Remote-Email": email, "Remote-Groups": groups}

    def get(self, path="/", method="GET", headers=None, **who) -> Reply:
        return self.raw(method, self.base + path, {**self.identity(**who), **(headers or {})})

    def post(self, path, fields=None, csrf=None, origin=ORIGIN, referer=None, headers=None, **who) -> Reply:
        fields = dict(fields or {})
        if csrf is VALID:
            csrf = self.app.csrf.issue(who.get("user", "denise"))
        if csrf is not None:
            fields["csrf"] = csrf
        send = {**self.identity(**who), "Content-Type": "application/x-www-form-urlencoded", **(headers or {})}
        if origin is not None:
            send["Origin"] = origin
        if referer is not None:
            send["Referer"] = referer
        return self.raw("POST", self.base + path, send, urllib.parse.urlencode(fields))

    def urls_in(self, html: str) -> list[str]:
        return re.findall(r"""(?:href|src|action)\s*=\s*["']([^"']*)["']""", html)

    def form_actions_in(self, html: str) -> list[str]:
        """The URLs of ``action`` attributes: routes that answer POST (a GET on them is a 405)."""
        return re.findall(r"""action\s*=\s*["']([^"']*)["']""", html)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


def start(tmp_path, base_path="/rosart", clock=None, extra_env=None) -> Harness:
    from publisher.config import load_config
    from publisher.jobs import JobStore
    from publisher.web import App, Response, make_server

    clock = clock or FakeClock()
    env = dict(ENV, PUBLISHER_BASE_PATH=base_path, PUBLISHER_DATA=str(tmp_path / "data"),
               PUBLISHER_BIND="127.0.0.1:0", **(extra_env or {}))
    config = load_config(env, tmp_dir=tmp_path / "tmp")
    store = JobStore(config.data / "publisher.sqlite3", site=config.site, clock=clock)
    app = App(config, store, clock, code_commit="deadbeef1234")
    server = make_server(app, config.bind)
    harness = Harness(base_path, config, store, app, server, clock, server.server_address[1])

    def probe(request):
        harness.probes.append(request)
        return Response(200, f"probe ok {request.method}", "text/plain; charset=utf-8")

    app.add_route("GET", "/probe", probe)
    app.add_route("POST", "/probe", probe)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    return harness
