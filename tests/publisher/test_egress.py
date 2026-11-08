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


# --- a whole fake check and publish under the strict guard (spec 006 T069; SC-006, code half) -------------

class _Cdn:
    """A fake CDN response: bytes, usable as a context manager like a urllib response."""

    def __init__(self, data=b"image-bytes"):
        self._data = data

    def read(self):
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class CdnInner:
    def __init__(self):
        self.urls = []

    def open(self, request, data=None, timeout=None):
        self.urls.append(request.full_url)
        return _Cdn()


@pytest.fixture
def strict_guard(monkeypatch):
    """Stricter than the autouse guard: *any* connection or name lookup fails, loopback included."""
    import socket

    def refuse(*args, **kwargs):
        raise AssertionError("a code path opened its own connection: everything must go through the fakes")

    for name in ("getaddrinfo", "create_connection"):
        monkeypatch.setattr(socket, name, refuse)
    for name in ("connect", "connect_ex"):
        monkeypatch.setattr(socket.socket, name, refuse)


def test_the_strict_guard_itself_refuses_a_loopback_connection(strict_guard):
    import socket

    with pytest.raises(AssertionError, match="own connection"):
        socket.create_connection(("127.0.0.1", 9))


def test_a_full_fake_check_and_publish_open_no_connection_and_only_allowlisted_names_are_asked(strict_guard, tmp_path):
    from flow_harness import Flow
    from publisher.egress import EgressOpener
    from publisher.slack import SlackNotifier
    from fake_slack import FakeSlack
    from theme_admin import THEME_URL_HOSTS

    flow = Flow(tmp_path)
    allowed = EgressOpener((flow.shopify.shop, *THEME_URL_HOSTS, "slack.com"), inner=flow.shopify.opener)
    names = []

    def make_shopify(deadline):
        from shopify_admin import AdminApiRunner, token_source_from_config

        tokens = token_source_from_config(flow.config.token_config(), shop=flow.shopify.shop, opener=allowed,
                                          clock=flow.clock.mono)
        return tokens, AdminApiRunner(flow.shopify.shop, tokens, allowed, flow.clock.sleep, deadline,
                                      clock=flow.clock.mono)

    job, _ = flow.check(make_shopify=make_shopify)
    assert job.state == "passed", job.reason_text
    published, _ = flow.publish(job.snapshot_id, make_shopify=make_shopify)
    assert published.state == "published", published.reason_text

    slack = FakeSlack()
    notifier = SlackNotifier(flow.config, flow.store, slack.opener, flow.clock.sleep, flow.scrubber)
    assert notifier.notify(published.id) == "posted"

    from urllib.parse import urlsplit
    names = {urlsplit(r.url).hostname for r in flow.shopify.requests} | {urlsplit(r.url).hostname for r in slack.requests}
    assert names == {flow.shopify.shop, "slack.com"}


def test_the_services_downloader_goes_through_the_egress_opener_and_refuses_the_public_site(tmp_path):
    from flow_harness import Flow
    from publisher.__main__ import make_deps_factory
    from publisher.egress import EgressOpener

    flow = Flow(tmp_path)
    inner = CdnInner()
    opener = EgressOpener(("cdn.shopify.com",), inner=inner)
    factory = make_deps_factory(flow.config, flow.store, flow.snapshots, flow.clock, flow.scrubber, "deadbeef1234",
                                opener, app_root=flow.image, aws_executable="aws")
    job = flow.start("check")
    deps = factory(job)

    try:
        assert deps.fetch("https://cdn.shopify.com/s/files/1/a.jpg") == b"image-bytes"
        assert inner.urls == ["https://cdn.shopify.com/s/files/1/a.jpg"], "the download went through the opener"
        for refused in ("https://rosart.com.au/", "https://www.rosart.com.au/products/x", "https://example.org/a.jpg"):
            with pytest.raises(ShopifyAdminError) as caught:
                deps.fetch(refused)
            assert caught.value.code == "egress-refused"
        assert len(inner.urls) == 1
    finally:
        deps.log.close()


def test_the_downloader_marks_throttling_and_server_errors_retryable_and_other_http_errors_not(tmp_path):
    import io
    import urllib.error

    from downloads import FetchError
    from publisher.egress import EgressOpener, make_opener_fetch

    class Failing:
        def __init__(self, error):
            self.error = error

        def open(self, request, data=None, timeout=None):
            raise self.error

    url = "https://cdn.shopify.com/s/files/1/a.jpg"
    expected = {404: False, 403: False, 429: True, 500: True, 503: True}
    for status, retryable in expected.items():
        error = urllib.error.HTTPError(url, status, "fake", {}, io.BytesIO(b""))
        fetch = make_opener_fetch(EgressOpener(("cdn.shopify.com",), inner=Failing(error)))
        with pytest.raises(FetchError) as caught:
            fetch(url)
        assert caught.value.retryable is retryable, status

    fetch = make_opener_fetch(EgressOpener(("cdn.shopify.com",), inner=Failing(ConnectionResetError("reset"))))
    with pytest.raises(FetchError) as caught:
        fetch(url)
    assert caught.value.retryable is True


def test_the_downloader_refuses_the_stores_own_host_and_slack_even_though_the_opener_allows_them():
    from publisher.egress import default_opener, make_opener_fetch

    fetch = make_opener_fetch(default_opener(SHOP, inner=RecordingInner()))

    for url in (f"https://{SHOP}/admin/api/2026-10/graphql.json", "https://slack.com/api/chat.postMessage"):
        with pytest.raises(ShopifyAdminError) as caught:
            fetch(url)
        assert caught.value.code == "egress-refused"
