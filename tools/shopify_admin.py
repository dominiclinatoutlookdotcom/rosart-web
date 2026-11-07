"""Read-only Shopify Admin API access over HTTPS, with a token source (spec 006, hand-off R-1).

``AdminApiRunner`` is a runner ``(query, variables) -> data`` like ``ShopifyCliRunner``
(tools/shopify_cli.py), so ``run_export`` takes it unchanged. The token comes from a
``TokenSource``: the client credentials grant (default) or a stored admin token. Both refuse any
``write_`` scope and any missing read scope before the first content query, mutations are refused
before any request, and the token is held in a ``Secret`` so it never reaches a log line, an
exception message or a ``repr``.

Failure codes (``ShopifyAdminError.code``) are the ones the publisher service translates:
``shopify-access``, ``shopify-not-permitted``, ``shopify-write-scope``, ``deadline``.
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable, Optional, Protocol

from shopify_cli import ShopifyCliError, _unwrap, assert_read_only

log = logging.getLogger("rosart.shopify_admin")

API_VERSION = "2026-10"

# The read scopes the export needs (owner list, 2026-11-07); kept equal to
# tools/publish_doctor.py REQUIRED_SCOPES by a test. read_legal_policies is accepted, never required.
REQUIRED_READ_SCOPES = ("read_content", "read_products", "read_online_store_navigation", "read_files",
                        "read_themes", "read_metaobjects")
OPTIONAL_READ_SCOPES = ("read_legal_policies",)

REFRESH_MARGIN_S = 30 * 60        # a token is reused while more than this remains
REQUEST_TIMEOUT_S = 60.0
MAX_THROTTLE_WAITS = 5
BACKOFF_S = (1, 2, 4, 8)          # waits before the retries of an HTTP 429, 5xx or network failure
USER_AGENT = "rosart-web-export/1"

WRONG_PERMISSIONS = "the website's Shopify access has the wrong permissions: contact the owner"


class ShopifyAdminError(ShopifyCliError):
    """A Shopify access failure with a plain code and a message free of secrets."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Secret:
    """Holds a credential; ``repr``, ``str`` and formatting never show it."""

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        self._value = value

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "[redacted]"

    __str__ = __repr__

    def __reduce__(self):
        raise TypeError("a Secret cannot be pickled")


class Opener(Protocol):
    """The ``open(request, timeout=)`` shape of ``urllib.request.OpenerDirector``."""

    def open(self, request: urllib.request.Request, data: Any = None, timeout: Optional[float] = None) -> Any: ...


class TokenSource(Protocol):
    def get(self) -> Secret:
        """A token whose scopes were checked read-only before it is first returned."""

    def invalidate(self) -> None:
        """Called after a 401."""


def parse_scopes(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        return [str(item) for item in value]
    return [item for item in re.split(r"[,\s]+", str(value or "")) if item]


def check_scopes(granted: list[str]) -> None:
    """Refuse a token that can write or that lacks a read scope the export needs."""
    writes = sorted(scope for scope in granted if scope.startswith("write_"))
    missing = [scope for scope in REQUIRED_READ_SCOPES if scope not in granted]
    if writes or missing:
        details = []
        if writes:
            details.append("write scopes: " + ", ".join(writes))
        if missing:
            details.append("missing read scopes: " + ", ".join(missing))
        raise ShopifyAdminError("shopify-write-scope", f"{WRONG_PERMISSIONS} ({'; '.join(details)})")


def graphql_url(shop: str) -> str:
    return f"https://{shop}/admin/api/{API_VERSION}/graphql.json"


def _error_code_of(exc: urllib.error.HTTPError) -> str:
    try:
        body = json.loads(exc.read().decode("utf-8", errors="replace"))
        return str(body.get("error", "")) if isinstance(body, dict) else ""
    except (ValueError, OSError):
        return ""


class ClientCredentialsTokenSource:
    """The client credentials grant: a 24 h token, cached in memory, refreshed under 30 min left."""

    def __init__(self, shop: str, client_id: str, client_secret: Secret, opener: Opener,
                 clock: Callable[[], float]) -> None:
        self._shop = shop
        self._client_id = client_id
        self._client_secret = client_secret
        self._opener = opener
        self._clock = clock
        self._token: Optional[Secret] = None
        self._expires_at = 0.0

    def __repr__(self) -> str:
        return f"ClientCredentialsTokenSource(shop={self._shop!r})"

    def get(self) -> Secret:
        if self._token is not None and self._expires_at - self._clock() > REFRESH_MARGIN_S:
            return self._token
        self._token = None
        self._token, self._expires_at = self._grant()
        return self._token

    def invalidate(self) -> None:
        self._token = None

    def _grant(self) -> tuple[Secret, float]:
        form = urllib.parse.urlencode({"grant_type": "client_credentials", "client_id": self._client_id,
                                       "client_secret": self._client_secret.reveal()})
        request = urllib.request.Request(
            f"https://{self._shop}/admin/oauth/access_token", data=form.encode("ascii"), method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json",
                     "User-Agent": USER_AGENT})
        try:
            with self._opener.open(request, timeout=30) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            reason = _error_code_of(exc)
            if reason == "shop_not_permitted":
                raise ShopifyAdminError(
                    "shopify-not-permitted",
                    "Shopify refused the website's access for this store (shop_not_permitted): contact the owner"
                ) from None
            raise ShopifyAdminError(
                "shopify-access",
                f"Shopify refused the website's access (HTTP {exc.code} {reason or 'error'}): contact the owner"
            ) from None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ShopifyAdminError("shopify-access", f"could not get a Shopify token ({type(exc).__name__})") from None
        token = body.get("access_token") if isinstance(body, dict) else None
        if not token:
            raise ShopifyAdminError("shopify-access", "Shopify answered the token request without a token")
        check_scopes(parse_scopes(body.get("scope")))
        expires_in = float(body.get("expires_in") or 0)
        log.info("shopify token granted for %s, valid %.0f s", self._shop, expires_in)
        return Secret(token), self._clock() + expires_in


class AdminApiRunner:
    """Runs read-only GraphQL against one store's Admin API; satisfies ``shopify_cli.Runner``."""

    API_VERSION = API_VERSION

    def __init__(self, shop: str, tokens: TokenSource, opener: Opener, sleep: Callable[[float], None],
                 deadline: float, clock: Optional[Callable[[], float]] = None) -> None:
        self._shop = shop
        self._tokens = tokens
        self._opener = opener
        self._sleep = sleep
        self._deadline = deadline
        self._clock = clock or time.monotonic

    def __repr__(self) -> str:
        return f"AdminApiRunner(shop={self._shop!r}, api={API_VERSION!r})"

    def __call__(self, query: str, variables: dict) -> dict:
        assert_read_only(query)
        throttle_waits = 0
        backoffs = 0
        regranted = False
        while True:
            self._check_deadline()
            token = self._tokens.get()
            status, result = self._post(token, query, variables)
            if status == 401:
                self._tokens.invalidate()
                if regranted:
                    raise ShopifyAdminError("shopify-access",
                                            "Shopify refused the website's access token twice: contact the owner")
                regranted = True
                continue
            if status == 429 or status >= 500 or status == 0:
                if backoffs >= len(BACKOFF_S):
                    raise ShopifyAdminError("shopify-access", "Shopify is not answering: try again later")
                self._wait(BACKOFF_S[backoffs], f"HTTP {status}" if status else "a network failure")
                backoffs += 1
                continue
            if status >= 400:
                raise ShopifyAdminError("shopify-access", f"Shopify answered HTTP {status}: contact the owner")
            throttle = _throttle_wait(result)
            if throttle is not None:
                if throttle_waits >= MAX_THROTTLE_WAITS:
                    raise ShopifyAdminError("shopify-access",
                                            f"Shopify kept throttling the export ({MAX_THROTTLE_WAITS} waits)")
                self._wait(throttle, "THROTTLED")
                throttle_waits += 1
                continue
            if "errors" in result and "data" not in result:
                result = dict(result, data=None)
            return _unwrap(result)

    def _check_deadline(self) -> None:
        if self._clock() >= self._deadline:
            raise ShopifyAdminError("deadline", "the time allowed for this job ran out")

    def _wait(self, seconds: float, why: str) -> None:
        if self._clock() + seconds >= self._deadline:
            raise ShopifyAdminError("deadline", "the time allowed for this job ran out")
        log.warning("shopify admin: %s, waiting %.1f s", why, seconds)
        self._sleep(seconds)

    def _post(self, token: Secret, query: str, variables: dict) -> tuple[int, dict]:
        """One request; returns ``(status, body)``. Status 0 means the network failed."""
        request = urllib.request.Request(
            graphql_url(self._shop), data=json.dumps({"query": query, "variables": variables}).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json",
                     "User-Agent": USER_AGENT, "X-Shopify-Access-Token": token.reveal()})
        timeout = max(1.0, min(REQUEST_TIMEOUT_S, self._deadline - self._clock()))
        try:
            with self._opener.open(request, timeout=timeout) as response:
                raw = response.read()
                status = response.status
        except urllib.error.HTTPError as exc:
            return exc.code, {}
        except (urllib.error.URLError, OSError):
            return 0, {}
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError:
            raise ShopifyAdminError("shopify-access", "Shopify returned a response that is not JSON") from None
        if not isinstance(body, dict):
            raise ShopifyAdminError("shopify-access", "Shopify returned an unexpected response")
        return status, body


def _throttle_wait(result: dict) -> Optional[float]:
    """Seconds to wait when the response is a GraphQL THROTTLED error, else None."""
    errors = result.get("errors")
    if not isinstance(errors, list):
        return None
    if not any(isinstance(e, dict) and (e.get("extensions") or {}).get("code") == "THROTTLED" for e in errors):
        return None
    cost = (result.get("extensions") or {}).get("cost") or {}
    status = cost.get("throttleStatus") or {}
    try:
        wait = (float(cost["requestedQueryCost"]) - float(status["currentlyAvailable"])) / float(status["restoreRate"])
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        return 1.0
    return round(wait, 3) if wait > 0 else 1.0
