"""A fake Slack Web API for the publisher tests (spec 006 T065; FR-023: no test touches Slack).

``FakeSlack.opener`` has the ``open(request, data=None, timeout=None)`` shape of the service's egress opener,
raises like ``urllib`` for HTTP errors, timeouts and refused connections, and records every request (URL,
headers, body, timeout). Outcomes are scripted in order with the ``*_next`` methods; with none left it answers
``{"ok": true}``.
"""
from __future__ import annotations

import io
import json
import socket
import urllib.error
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class SlackRequest:
    url: str
    headers: dict
    body: str
    timeout: Optional[float]

    def json(self) -> dict:
        return json.loads(self.body)


class _Reply:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeSlack:
    def __init__(self) -> None:
        self.requests: list[SlackRequest] = []
        self._script: list[Callable[[], _Reply]] = []
        self.opener = self

    # --- scripting -------------------------------------------------------------------------------
    def reply_next(self, payload: dict, times: int = 1) -> None:
        for _ in range(times):
            self._script.append(lambda p=payload: _Reply(200, json.dumps(p).encode()))

    def invalid_auth_next(self, times: int = 1) -> None:
        self.reply_next({"ok": False, "error": "invalid_auth"}, times)

    def http_error_next(self, status: int = 500, times: int = 1) -> None:
        def raise_error():
            raise urllib.error.HTTPError("https://slack.com/api/chat.postMessage", status, "fake", {},
                                         io.BytesIO(b"{}"))
        self._script.extend([raise_error] * times)

    def timeout_next(self, times: int = 1) -> None:
        def raise_timeout():
            raise socket.timeout("timed out")
        self._script.extend([raise_timeout] * times)

    def refused_next(self, times: int = 1) -> None:
        def raise_refused():
            raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
        self._script.extend([raise_refused] * times)

    # --- the opener --------------------------------------------------------------------------------
    def open(self, request: Any, data: Any = None, timeout: Optional[float] = None) -> _Reply:
        body = (data if data is not None else request.data) or b""
        self.requests.append(SlackRequest(request.full_url, {k.lower(): v for k, v in request.header_items()},
                                          body.decode("utf-8"), timeout))
        if self._script:
            return self._script.pop(0)()
        return _Reply(200, json.dumps({"ok": True, "ts": "1.0"}).encode())

    @property
    def texts(self) -> list[str]:
        return [r.json().get("text", "") for r in self.requests]
