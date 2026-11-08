"""The outbound allowlist, enforced in code (spec 006 T037; research R-11, FR-017).

Seam: ``publisher.egress.EgressOpener`` (the ``open(request, data=, timeout=)`` shape every HTTP user
in the service takes) and ``default_opener(shop)``. Redirects are checked on a real loopback server.
"""
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from shopify_admin import ShopifyAdminError

SHOP = "rosart-mhfmyuxq.myshopify.com"


class RecordingInner:
    """Stands in for the real opener: records what got through and answers 200."""

    def __init__(self):
        self.urls = []

    def open(self, request, data=None, timeout=None):
        self.urls.append(request.full_url if hasattr(request, "full_url") else request)
        return "response"


def make(inner=None):
    from publisher.egress import default_opener

    inner = inner or RecordingInner()
    return default_opener(SHOP, inner=inner), inner


@pytest.mark.parametrize("url", [
    f"https://{SHOP}/admin/api/2026-10/graphql.json",
    f"https://{SHOP}/admin/oauth/access_token",
    "https://cdn.shopify.com/s/files/1/0001/image.jpg",
    "https://slack.com/api/chat.postMessage",
])
def test_the_stores_admin_host_the_shopify_cdn_and_slack_are_allowed(url):
    opener, inner = make()

    assert opener.open(urllib.request.Request(url)) == "response"
    assert inner.urls == [url]


@pytest.mark.parametrize("url, host", [
    ("https://rosart.com.au/", "rosart.com.au"),
    ("https://www.rosart.com.au/products/x", "www.rosart.com.au"),
    ("https://rosart-mhfmyuxq.myshopify.com.evil.example/", "rosart-mhfmyuxq.myshopify.com.evil.example"),
    ("https://other-shop.myshopify.com/admin/api/2026-10/graphql.json", "other-shop.myshopify.com"),
    ("https://example.com/", "example.com"),
])
def test_any_other_host_is_refused_naming_the_host_and_nothing_is_sent(url, host):
    opener, inner = make()

    with pytest.raises(ShopifyAdminError) as caught:
        opener.open(urllib.request.Request(url))

    assert caught.value.code == "egress-refused" and host in str(caught.value)
    assert inner.urls == []


@pytest.mark.parametrize("url", [
    f"http://{SHOP}/admin/api/2026-10/graphql.json",           # not https
    f"https://{SHOP}:8443/admin/api/2026-10/graphql.json",     # another port
    f"https://user:pw@{SHOP}/admin/api/2026-10/graphql.json",  # credentials in the URL
    "ftp://cdn.shopify.com/x",
])
def test_the_scheme_the_port_and_url_credentials_are_part_of_the_rule(url):
    opener, inner = make()

    with pytest.raises(ShopifyAdminError) as caught:
        opener.open(urllib.request.Request(url))

    assert caught.value.code == "egress-refused"
    assert inner.urls == []


def test_a_plain_url_string_is_checked_too():
    opener, inner = make()

    with pytest.raises(ShopifyAdminError):
        opener.open("https://rosart.com.au/")
    assert inner.urls == []


def test_the_theme_url_hosts_are_the_one_constant_of_theme_admin():
    import theme_admin
    from publisher import egress

    assert egress.THEME_URL_HOSTS is theme_admin.THEME_URL_HOSTS
    opener, _ = make()
    assert set(opener.allowed_hosts) == {SHOP, "slack.com", *theme_admin.THEME_URL_HOSTS}


class RedirectingServer:
    """A loopback server whose / answers 302 to the ``target`` and whose /ok answers 200."""

    def __init__(self, target):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                outer.hits.append(self.path)
                if self.path == "/ok":
                    self.send_response(200)
                    self.send_header("Content-Length", "2")
                    self.end_headers()
                    self.wfile.write(b"ok")
                else:
                    self.send_response(302)
                    self.send_header("Location", outer.target)
                    self.send_header("Content-Length", "0")
                    self.end_headers()

            def log_message(self, *args):
                pass

        self.hits = []
        self.target = target
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def loopback_opener(port):
    from publisher.egress import EgressOpener

    return EgressOpener([f"127.0.0.1:{port}"], schemes=("http",))


def test_a_redirect_to_another_host_is_refused_naming_that_host():
    with RedirectingServer(target="http://localhost:1/steal") as server:
        opener = loopback_opener(server.port)
        with pytest.raises(ShopifyAdminError) as caught:
            opener.open(urllib.request.Request(f"http://127.0.0.1:{server.port}/"))

    assert caught.value.code == "egress-refused" and "localhost" in str(caught.value)
    assert server.hits == ["/"]


def test_a_redirect_that_stays_on_an_allowed_host_is_followed():
    with RedirectingServer(target="/ok") as server:
        opener = loopback_opener(server.port)
        with opener.open(urllib.request.Request(f"http://127.0.0.1:{server.port}/")) as response:
            assert response.read() == b"ok"

    assert server.hits == ["/", "/ok"]
