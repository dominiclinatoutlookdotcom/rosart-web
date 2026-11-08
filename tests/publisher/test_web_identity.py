"""Identity from the Remote-* headers, methods, body size, caching (spec 006 T045; contracts/http-routes.md
"Request rules"; research R-16; FR-001, FR-004, SC-003).

Seam: the real server on 127.0.0.1:0 (fixture ``web``).
"""
import pytest

NO_IDENTITY = "No sign-in identity reached the publisher; contact the owner"
NO_ACCESS = "You do not have access to publishing; ask the owner"


@pytest.mark.parametrize("method", ["GET", "POST"])
def test_no_remote_user_gives_403_with_the_plain_page(web, method):
    reply = web.get("/", user=None) if method == "GET" else web.post("/probe", user=None)

    assert reply.status == 403 and NO_IDENTITY in reply.body and "<html" in reply.body


@pytest.mark.parametrize("blank", ["", "   "])
def test_an_empty_remote_user_counts_as_none(web, blank):
    assert web.get("/", user=blank).status == 403
    assert web.raw("GET", web.base + "/", {"Remote-User": blank}).status == 403


def test_a_signed_in_user_reaches_the_home_page_and_the_probe(web):
    assert web.get("/").status == 200
    assert web.get("/probe").status == 200 and len(web.probes) == 1


def test_the_request_carries_user_name_email_and_groups(web):
    web.get("/probe", user="denise", name="Denise Hill", email="d@example.org", groups="a, rosart-publishers ,b")

    request = web.probes[-1]
    assert (request.user, request.name, request.email) == ("denise", "Denise Hill", "d@example.org")
    assert request.groups == ["a", "rosart-publishers", "b"]


def test_the_name_and_email_fall_back_when_empty(web):
    web.get("/probe", user="denise", name="", email="")

    request = web.probes[-1]
    assert request.name == "denise" and request.email == "denise@rosart-publisher.invalid"


def test_a_utf8_name_sent_as_raw_bytes_is_read_as_utf8(web):
    web.raw("GET", web.base + "/probe", {"Remote-User": "zoe", "Remote-Name": "Zoë".encode("utf-8").decode("latin-1")})

    assert web.probes[-1].name == "Zoë"


def test_a_post_without_the_publisher_group_is_403_and_nothing_runs(web):
    for groups in ("", "other-group", "rosart-publishers-extra"):
        reply = web.post("/probe", groups=groups)

        assert reply.status == 403 and NO_ACCESS in reply.body
    assert web.probes == [] and web.store.running() is None


def test_a_get_needs_no_group(web):
    assert web.get("/", groups="").status == 200


@pytest.mark.parametrize("path", ["/healthz", "/static/app.css"])
def test_the_open_paths_ignore_identity(web, path):
    assert web.get(path, user=None).status == 200
    assert web.get(path, user="denise", groups="").status == 200
    assert web.raw("GET", web.base + path, {"Remote-User": "xÿ"}).status == 200


def test_the_status_json_path_is_open_too(web):
    # The route itself arrives with the portal tile (US4); it must already not ask for sign-in.
    assert web.get("/status.json", user=None).status == 404


@pytest.mark.parametrize("method", ["PUT", "DELETE", "PATCH", "OPTIONS"])
def test_other_methods_are_405_with_the_allowed_list(web, method):
    for path in ("/", "/healthz", "/probe"):
        reply = web.get(path, method=method)

        assert reply.status == 405, (method, path)
        assert reply.headers["allow"] == "GET, HEAD, POST"


def test_head_gives_the_headers_of_get_and_no_body(web):
    get = web.get("/")
    head = web.get("/", method="HEAD")

    assert head.status == 200 and head.raw == b""
    assert head.headers["content-type"] == get.headers["content-type"]


def test_a_post_to_a_page_that_only_reads_is_405_not_a_state_change(web):
    reply = web.post("/healthz")

    assert reply.status == 405


def test_a_body_over_4_kib_is_refused_before_anything_runs(web):
    reply = web.post("/probe", {"padding": "x" * 5000})

    assert reply.status == 413 and web.probes == []


def test_a_body_of_exactly_4_kib_is_accepted(web):
    fields_size = len("padding=")
    reply = web.raw("POST", web.base + "/probe", {**web.identity(), "Content-Type": "application/x-www-form-urlencoded"},
                    "padding=" + "x" * (4096 - fields_size))

    assert reply.status != 413


def test_a_body_that_is_not_a_form_is_refused(web):
    reply = web.raw("POST", web.base + "/probe", {**web.identity(), "Content-Type": "application/json"}, "{}")

    assert reply.status == 415 and web.probes == []


def test_html_and_json_are_never_cached(web):
    assert web.get("/").headers["cache-control"] == "no-store"
    assert web.get("/healthz").headers["cache-control"] == "no-store"
    assert web.get("/no-such-page").headers["cache-control"] == "no-store"
    assert web.get("/", user=None).headers["cache-control"] == "no-store"


def test_an_error_in_a_handler_shows_no_stack_trace(web):
    from publisher.web import Response  # noqa: F401 - the seam under test

    def boom(request):
        raise RuntimeError("secret internal detail /app/publisher/web.py")

    web.app.add_route("GET", "/boom", boom)
    reply = web.get("/boom")

    assert reply.status == 500
    assert "secret internal detail" not in reply.body and "Traceback" not in reply.body and "/app/" not in reply.body
