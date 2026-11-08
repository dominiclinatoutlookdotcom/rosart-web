"""The one outbound HTTP opener of the service: an allowlist enforced in code (spec 006 T038; research R-11).

Everything that makes an HTTP request from Python (the Admin API runner, the image downloader, the
theme file fetch, Slack) takes an opener with ``open(request, data=None, timeout=None)``. This one
refuses any URL whose scheme, host, port or credentials are not on the list, before a socket is
opened, and checks every redirect again. The public site and the storefront are not on it.
"""
from __future__ import annotations

import urllib.parse
import urllib.request
from typing import Any, Iterable, Optional

from shopify_admin import ShopifyAdminError
from theme_admin import THEME_URL_HOSTS

SLACK_HOST = "slack.com"
DEFAULT_PORTS = {"http": 80, "https": 443}


class EgressRefused(ShopifyAdminError):
    """A request to a host the service may not call; ``host`` names it."""

    def __init__(self, host: str, reason: str) -> None:
        super().__init__("egress-refused", f"refusing to contact {host or 'an unknown host'}: {reason}")
        self.host = host


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    def __init__(self, check) -> None:
        self._check = check

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D102 - urllib hook
        self._check(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class EgressOpener:
    """Opens a URL only when it is on the allowlist; ``allowed`` holds ``host`` or ``host:port`` entries."""

    def __init__(self, allowed: Iterable[str], schemes: tuple[str, ...] = ("https",), inner: Optional[Any] = None) -> None:
        self.allowed_hosts = tuple(allowed)
        self._schemes = schemes
        self._inner = inner or urllib.request.build_opener(_CheckedRedirects(self.check))

    def check(self, url: str) -> None:
        """Raise :class:`EgressRefused` unless ``url`` is allowed."""
        parts = urllib.parse.urlsplit(url)
        host = (parts.hostname or "").lower()
        if parts.scheme not in self._schemes:
            raise EgressRefused(host, f"only {', '.join(self._schemes)} is allowed, not {parts.scheme or 'no scheme'}")
        if parts.username or parts.password:
            raise EgressRefused(host, "credentials in a URL are not allowed")
        try:
            port = parts.port
        except ValueError:
            raise EgressRefused(host, "the port is not valid") from None
        for entry in self.allowed_hosts:
            entry_host, _, entry_port = entry.partition(":")
            if host != entry_host.lower():
                continue
            wanted = int(entry_port) if entry_port else DEFAULT_PORTS.get(parts.scheme)
            if (port if port is not None else DEFAULT_PORTS.get(parts.scheme)) == wanted:
                return
        raise EgressRefused(host, "it is not on the outbound allowlist")

    def open(self, request: Any, data: Any = None, timeout: Optional[float] = None) -> Any:
        self.check(request if isinstance(request, str) else request.full_url)
        if timeout is None:
            return self._inner.open(request, data)
        return self._inner.open(request, data, timeout)


def default_opener(shop: str, inner: Optional[Any] = None) -> EgressOpener:
    """The service's opener: the store's Admin API host, the theme/image CDN hosts and Slack."""
    return EgressOpener((shop, *THEME_URL_HOSTS, SLACK_HOST), inner=inner)
