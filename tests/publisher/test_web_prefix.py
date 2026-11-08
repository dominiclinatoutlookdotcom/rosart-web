"""The service routes its own path prefix (spec 006 T045; contracts/http-routes.md "Path prefix", research R-16).

Seam: the real server on 127.0.0.1:0 (fixture ``web``, run under ``/rosart`` and ``/other``).
"""
import re


def test_a_path_outside_the_prefix_is_a_plain_404(web):
    for path in ("/", "/jobs", web.base + "x/", "/elsewhere/", "/static/app.css"):
        reply = web.raw("GET", path, web.identity())
        assert reply.status == 404, path
        assert "text/html" in reply.headers["content-type"]


def test_the_bare_prefix_redirects_to_the_prefix_with_a_slash(web):
    reply = web.raw("GET", web.base, web.identity())

    assert reply.status == 301 and reply.headers["location"] == web.base + "/"


def test_the_home_page_is_served_at_the_prefix(web):
    reply = web.get("/")

    assert reply.status == 200 and "text/html" in reply.headers["content-type"]


def test_an_unknown_route_under_the_prefix_is_a_404_page(web):
    reply = web.get("/no-such-page")

    assert reply.status == 404 and "<html" in reply.body


def test_every_url_a_page_emits_starts_with_the_prefix_and_resolves_to_a_route(web):
    pages = [web.get("/"), web.get("/no-such-page"), web.get("/", user=None), web.get("/", method="DELETE")]
    seen = set()
    for page in pages:
        for url in web.urls_in(page.body):
            seen.add(url)
            assert url.startswith(web.base + "/"), url
            assert not url.startswith("//") and "://" not in url
    assert seen, "the pages should link at least their stylesheet"
    for url in seen:
        assert web.raw("GET", url, web.identity()).status == 200, url


def test_the_redirect_location_is_path_absolute_with_no_host(web):
    location = web.raw("GET", web.base, web.identity()).headers["location"]

    assert re.fullmatch(r"/[^:]*", location) and "//" not in location
