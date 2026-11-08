"""No state but published and published-cache-pending is ever shown as success; a partial or interrupted publish
always says the live site may be partly updated (spec 006 T095; FR-010, SC-005).

Seam: every final state of every kind, written through the job store and rendered by the real server (home page, job
page, ``/status.json``) and by the Slack notifier. Run under ``/rosart`` and ``/other``.
"""
import json
import re

import pytest

PARTLY = "the live site may be partly updated"
CASES = [("check", "passed"), ("check", "failed"), ("check", "interrupted"),
         ("publish", "published"), ("publish", "published-cache-pending"), ("publish", "failed"),
         ("publish", "publish-failed-partial"), ("publish", "interrupted")]
SUCCESS = {("publish", "published"), ("publish", "published-cache-pending")}
PARTIAL = {("publish", "publish-failed-partial"), ("publish", "interrupted")}
SUMMARY = json.dumps({"uploaded": 3, "deleted": 1, "changes": "1 product changed"})


@pytest.fixture(params=CASES, ids=[f"{k}-{s}" for k, s in CASES])
def case(request, web):
    kind, state = request.param
    job = web.store.start_job(kind, "maria", "Maria Lopez", "maria@example.org", "abc1234", 60)
    values = {"summary_json": SUMMARY, "invalidation_id": "I2ABC"} if (kind, state) in SUCCESS else {}
    if state not in ("passed", "published", "published-cache-pending"):
        values.update(reason_code="unknown", reason_text="something went wrong", notice_state="pending")
    web.store.finish(job.id, state, **values)
    return web, web.store.get(job.id), kind, state


def heading(body):
    return re.search(r"<h1>(.*?)</h1>", body, re.S).group(1)


def slack_text(web, job):
    from publisher.slack import SlackNotifier

    notifier = SlackNotifier(web.config, web.store, None, lambda s: None)
    return notifier.message(job)


def test_only_the_two_published_states_read_as_success_on_the_job_page_and_the_home_page(case):
    web, job, kind, state = case
    page = web.get(f"/jobs/{job.id}", user="maria").body
    home = web.get("/", user="maria").body

    success = (kind, state) in SUCCESS
    assert bool(re.search(r"\b(Live|Published)\b", heading(page))) is success, heading(page)
    assert bool(re.search(r"\b(Live|Published)\b", home)) is success
    if success:
        assert "I2ABC" in page


def test_only_the_two_published_states_read_as_success_in_slack(case):
    web, job, kind, state = case
    text = slack_text(web, job)

    if kind == "check" and state == "passed":
        assert text is None, "a passed check posts nothing"
        return
    assert (" published by " in text) is ((kind, state) in SUCCESS), text


def test_a_partial_or_interrupted_publish_always_says_the_live_site_may_be_partly_updated(case):
    web, job, kind, state = case
    page = web.get(f"/jobs/{job.id}", user="maria").body.lower()
    home = web.get("/", user="maria").body.lower()
    text = (slack_text(web, job) or "").lower()

    partial = (kind, state) in PARTIAL
    assert (PARTLY in page) is partial
    assert (PARTLY in home) is partial, "the home page's last publish carries it too"
    assert (PARTLY in text) is partial
    if partial:
        assert "mirror" in page, "the page says a new publish mirrors the site fully"


def test_status_json_matches(case):
    web, job, kind, state = case
    body = web.get("/status.json", user=None).json()

    if kind == "check":
        assert body["last_publish"] is None and body["needs_human"] is False
        return
    assert body["last_publish"]["result"] == ("ok" if (kind, state) in SUCCESS else "failed")
    assert body["needs_human"] is (state != "published")
