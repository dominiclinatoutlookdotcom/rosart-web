"""Page rules: security headers, mobile layout, no external asset, escaping (spec 006 T049; FR-020,
contracts/http-routes.md "Security headers").

Seams: every HTML response of the real server, ``Pages`` (layout and form helpers) and ``static/app.css``.
"""
import re
from pathlib import Path

import pytest

CSS = Path(__file__).resolve().parent.parent.parent / "publisher" / "static" / "app.css"
CSP = ("default-src 'none'; style-src 'self'; img-src 'self'; form-action 'self'; "
       "frame-ancestors 'none'; base-uri 'none'")


def html_replies(web):
    return {
        "home": web.get("/"),
        "not-found": web.get("/nope"),
        "no-identity": web.get("/", user=None),
        "no-group": web.post("/probe", groups=""),
        "bad-method": web.get("/", method="DELETE"),
        "redirect": web.raw("GET", web.base, web.identity()),
        "too-large": web.post("/probe", {"x": "y" * 5000}),
    }


def test_every_html_response_carries_the_csp_and_referrer_policy_and_no_frame_options(web):
    for name, reply in html_replies(web).items():
        assert "text/html" in reply.headers["content-type"], name
        assert reply.headers["content-security-policy"] == CSP, name
        assert reply.headers["referrer-policy"] == "same-origin", name
        assert "x-frame-options" not in reply.headers, name
        assert "strict-transport-security" not in reply.headers and "x-content-type-options" not in reply.headers, name


def test_the_home_page_has_a_viewport_meta_and_no_script_inline_style_or_handler(web):
    body = web.get("/").body

    assert '<meta name="viewport" content="width=device-width, initial-scale=1">' in body
    assert "<script" not in body.lower() and "style=" not in body.lower()
    assert not re.search(r"\son[a-z]+\s*=", body.lower())


def test_the_stylesheet_has_no_fixed_width_over_390_px():
    css = CSS.read_text(encoding="utf-8")

    for prop, value in re.findall(r"(?<![-\w])((?:min-)?width)\s*:\s*(\d+)px", css):
        assert int(value) <= 390, f"{prop}: {value}px"


def test_the_stylesheet_is_served_with_an_hour_of_cache_and_nothing_else_is_static(web):
    reply = web.get("/static/app.css", user=None)

    assert reply.status == 200 and reply.headers["content-type"].startswith("text/css")
    assert reply.headers["cache-control"] == "max-age=3600"
    assert reply.raw == CSS.read_bytes()
    for path in ("/static/other.css", "/static/", "/static/../web.py", "/static/%2e%2e/web.py", "/static/app.css/x"):
        assert web.get(path, user=None).status == 404, path


def test_the_layout_escapes_its_title_and_marks_the_page_for_phones():
    from publisher.pages import Pages

    html = Pages("/rosart").layout("<script>alert(1)</script> & co", "<p>body</p>")

    assert "<script>" not in html and "&lt;script&gt;alert(1)&lt;/script&gt; &amp; co" in html
    assert "<p>body</p>" in html and 'name="viewport"' in html


def test_text_from_outside_is_escaped_by_esc_including_quotes():
    from publisher.pages import esc

    assert esc('<b>"x"</b> & \'y\'') == "&lt;b&gt;&quot;x&quot;&lt;/b&gt; &amp; &#x27;y&#x27;"
    assert esc(None) == "" and esc(5) == "5"


def test_a_page_can_refresh_itself_only_when_asked():
    from publisher.pages import Pages

    pages = Pages("/rosart")

    assert 'http-equiv="refresh" content="5"' in pages.layout("Checking", "", refresh_s=5)
    assert "refresh" not in pages.layout("Done", "")


def test_the_url_helper_prefixes_every_path_and_refuses_a_path_without_a_slash():
    from publisher.pages import Pages

    assert Pages("/rosart").url("/jobs/42") == "/rosart/jobs/42"
    assert Pages("/other").url("/") == "/other/"
    with pytest.raises(ValueError):
        Pages("/rosart").url("jobs")


def test_a_form_has_a_labelled_button_the_csrf_field_and_a_prefixed_action():
    from publisher.pages import Pages

    form = Pages("/rosart").form("/check", "Check changes", "tok<en>", hidden={"snapshot": 'a"b'})

    assert 'action="/rosart/check"' in form and 'method="post"' in form
    assert '<button type="submit">Check changes</button>' in form
    assert 'name="csrf" value="tok&lt;en&gt;"' in form and 'name="snapshot" value="a&quot;b"' in form
    with pytest.raises(ValueError):
        Pages("/rosart").form("/check", "  ", "tok")


def test_no_page_links_or_loads_anything_outside_the_service(web):
    for name, reply in html_replies(web).items():
        for url in web.urls_in(reply.body):
            assert url.startswith(web.base + "/") and "://" not in url and not url.startswith("//"), (name, url)
