"""A failed check explains itself on the pages and in Slack (spec 006 T073; US2 scenarios 1 to 4, E3).

Seam: the real server over the whole fake world (``flow_web.FlowWeb``); the failing step is the fake ``npm``
printing the real message text of the site's checks. Run under ``/rosart`` and ``/other``.
"""
import re

import pytest

from flow_harness import FakeChildren
from flow_web import FlowWeb
from web_harness import VALID

MARIA = dict(user="maria", name="Maria Lopez", email="maria@example.org")
CHECK_DIST = ("npm", "run", "check:dist")
EMAIL_OUTPUT = ("dist check failed with 1 problem(s):\n  - contact/index.html (page /contact/): email address "
                "info@deniva.com.au is not the site email info@rosart.com.au\n")
THEME_OUTPUT = (" FAIL  src/lib/fidelity.test.ts > the site matches the theme\n"
                "AssertionError: theme s2 [section] is missing on the site\n")
ODD_OUTPUT = ("> astro build\nTypeError: cannot read properties of undefined (reading 'title')\n"
              "    at render (file:///work/src/pages/index.astro:12:5)\n")


@pytest.fixture(params=["/rosart", "/other"], ids=["base-rosart", "base-other"])
def failing(request, tmp_path):
    """A world whose ``npm run check:dist`` fails with the email-rule output (change ``children.fail`` per test)."""
    world = FlowWeb(tmp_path, base_path=request.param,
                    children=FakeChildren(fail={CHECK_DIST: (1, EMAIL_OUTPUT)}))
    yield world
    world.close()


def press_check(world):
    reply = world.web.post("/check", csrf=VALID, **MARIA)
    assert reply.status == 303, reply.body
    job_id = int(reply.headers["location"].rsplit("/", 1)[1])
    world.wait()
    return world.flow.store.get(job_id)


def job_page(world, job):
    return world.web.get(f"/jobs/{job.id}", **MARIA).body


def has_publish_button(body):
    return 'name="snapshot"' in body


def test_another_email_on_a_page_names_the_page_and_the_shopify_fix_and_offers_no_publish(failing):
    job = press_check(failing)

    page = job_page(failing, job)
    home = failing.web.get("/", **MARIA).body

    assert job.state == "failed" and job.reason_code == "email-rule"
    assert "Check failed: nothing was published" in page
    assert "/contact/" in page and "change the email to info@rosart.com.au in the Shopify admin" in page
    assert not has_publish_button(page) and not has_publish_button(home)


def test_an_unsupported_theme_change_says_contact_the_owner_and_keeps_the_failing_lines_in_the_log(failing):
    failing.flow.children.fail = {("npm", "test"): (1, THEME_OUTPUT)}

    job = press_check(failing)

    page = job_page(failing, job)
    log = (failing.flow.config.data / job.log_path).read_text(encoding="utf-8")
    assert job.state == "failed" and job.reason_code == "theme-fidelity"
    assert "contact the owner" in page
    assert "theme s2 [section] is missing on the site" in log, "the raw failing lines stay in the job log"
    assert "theme s2 [section] is missing on the site" not in page


def test_an_unrecognised_failure_quotes_the_key_line_and_never_shows_a_stack_trace(failing):
    failing.flow.children.fail = {("npm", "run", "build"): (1, ODD_OUTPUT)}

    job = press_check(failing)

    page = job_page(failing, job)
    assert job.reason_code == "unknown"
    assert "TypeError: cannot read properties of undefined (reading &#x27;title&#x27;)" in page
    assert "contact the owner" in page
    assert "at render" not in page and "index.astro:12" not in page


def test_a_failed_check_posts_one_slack_line_with_the_job_number_and_the_plain_reason(failing):
    job = press_check(failing)

    assert len(failing.slack.requests) == 1
    text = failing.slack.texts[0]
    assert f"job {job.id} check failed for Maria Lopez" in text
    assert "/contact/" in text and "Shopify admin" in text
    assert "deniva" not in text and "Traceback" not in text


def test_several_problems_are_each_listed_on_the_page(failing):
    failing.flow.children.fail = {CHECK_DIST: (1, EMAIL_OUTPUT.replace("1 problem", "2 problem") +
                                               "  - _astro/huge.png: image is 700 KB, over the 600 KB limit\n")}

    job = press_check(failing)

    page = job_page(failing, job)
    assert "/contact/" in page and "_astro/huge.png" in page


def test_the_home_page_shows_the_failed_check_and_still_offers_a_new_check(failing):
    press_check(failing)

    home = failing.web.get("/", **MARIA).body

    assert "The last check failed" in home and "/contact/" in home
    assert "Check changes" in home and "can still be published" not in home


def test_an_earlier_passed_check_is_still_offered_next_to_the_failure(failing):
    failing.flow.children.fail = {}
    passed = press_check(failing)
    failing.flow.children.fail = {CHECK_DIST: (1, EMAIL_OUTPUT)}
    failing.flow.clock.advance(600)
    failed = press_check(failing)

    home = failing.web.get("/", **MARIA).body

    assert passed.state == "passed" and failed.state == "failed"
    assert "The last check failed" in home and "/contact/" in home
    assert "the check from 20:00 can still be published until 21:00" in home
    assert re.search(rf'name="snapshot" value="{re.escape(passed.snapshot_id)}"', home), "Publish offers the old one"


def test_a_later_passed_check_clears_the_failure_from_the_home_page(failing):
    press_check(failing)
    failing.flow.children.fail = {}
    press_check(failing)

    home = failing.web.get("/", **MARIA).body

    assert "The last check failed" not in home and has_publish_button(home)
