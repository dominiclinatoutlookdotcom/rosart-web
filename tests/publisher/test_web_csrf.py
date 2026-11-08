"""CSRF: Origin or Referer, and an HMAC token bound to the user (spec 006 T047; FR-018, research R-14).

Seam: POST through the real server to the ``/probe`` route; "nothing started" means the handler was never
called and no job row exists.
"""
import pytest

from web_harness import ORIGIN, VALID

OUT_OF_DATE = "This page was out of date; reload it and press the button again"


def refused(reply, web):
    return reply.status == 403 and web.probes == [] and web.store.running() is None


def test_a_post_with_the_right_origin_and_a_valid_token_reaches_the_handler(web):
    reply = web.post("/probe", {"x": "1"}, csrf=VALID)

    assert reply.status == 200 and len(web.probes) == 1
    assert web.probes[0].form["x"] == "1"


@pytest.mark.parametrize("origin", [
    "https://outline.int.solagratia.com.au",
    "http://int.solagratia.com.au",
    "https://int.solagratia.com.au:8443",
    "https://int.solagratia.com.au.evil.example",
    "https://evil.example",
    "null",
    "",
])
def test_a_foreign_or_malformed_origin_is_refused(web, origin):
    reply = web.post("/probe", csrf=VALID, origin=origin, referer=ORIGIN + web.base + "/")

    assert refused(reply, web)


def test_without_origin_a_referer_under_the_publisher_path_is_accepted(web):
    reply = web.post("/probe", csrf=VALID, origin=None, referer=ORIGIN + web.base + "/jobs/4")

    assert reply.status == 200


@pytest.mark.parametrize("referer", [
    lambda base: ORIGIN + "/",
    lambda base: ORIGIN + "/kb/",
    lambda base: ORIGIN + base + "x/",
    lambda base: ORIGIN + base,
    lambda base: "https://outline.int.solagratia.com.au" + base + "/",
    lambda base: "https://int.solagratia.com.au.evil.example" + base + "/",
    lambda base: "",
])
def test_without_origin_any_other_referer_is_refused(web, referer):
    reply = web.post("/probe", csrf=VALID, origin=None, referer=referer(web.base))

    assert refused(reply, web)


def test_with_neither_origin_nor_referer_the_post_is_refused(web):
    assert refused(web.post("/probe", csrf=VALID, origin=None, referer=None), web)


@pytest.mark.parametrize("token", [None, "", "garbage", "!!!!", "A" * 80])
def test_a_missing_or_malformed_token_is_refused_with_the_out_of_date_message(web, token):
    reply = web.post("/probe", csrf=token)

    assert refused(reply, web) and OUT_OF_DATE in reply.body


def test_a_token_is_valid_for_twelve_hours_and_not_longer(web):
    token = web.app.csrf.issue("denise")

    web.clock.advance(12 * 3600 - 1)
    assert web.post("/probe", csrf=token).status == 200
    web.clock.advance(2)
    expired = web.post("/probe", csrf=token)

    assert expired.status == 403 and OUT_OF_DATE in expired.body and len(web.probes) == 1


def test_a_token_belongs_to_the_user_it_was_issued_to(web):
    token = web.app.csrf.issue("sam")

    reply = web.post("/probe", csrf=token, user="denise")

    assert refused(reply, web) and OUT_OF_DATE in reply.body


def test_a_token_made_before_a_restart_is_refused(web):
    from publisher.web import App

    token = web.app.csrf.issue("denise")
    restarted = App(web.config, web.store, web.clock)       # a new process: a new random key

    assert restarted.csrf.verify("denise", token) is False
    assert web.app.csrf.verify("denise", token) is True


def test_a_wall_clock_step_back_does_not_let_a_future_token_through(web):
    token = web.app.csrf.issue("denise")
    web.clock.step_wall(-3 * 3600)             # the token now looks as if it was issued three hours ahead

    reply = web.post("/probe", csrf=token)

    assert refused(reply, web)


def test_a_get_needs_neither_origin_nor_token(web):
    assert web.get("/probe").status == 200


def test_the_group_and_identity_checks_still_come_first(web):
    assert web.post("/probe", csrf=VALID, user=None).status == 403
    assert web.post("/probe", csrf=VALID, groups="").status == 403
    assert "do not have access" in web.post("/probe", csrf=VALID, groups="").body
