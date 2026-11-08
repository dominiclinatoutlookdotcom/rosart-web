"""The check and publish routes, the home page and the job page; the US1 independent test end to end
(spec 006 T067; contracts/http-routes.md routes GET /, POST /check, POST /publish, GET /jobs/<id>).

Seam: the real server on 127.0.0.1:0 over the whole fake world (``flow_web.FlowWeb``), run under ``/rosart``
and ``/other``. Jobs run on the runner's worker thread; Slack, Shopify, AWS and git are fakes or temp folders.
"""
import json
import re
import threading

import pytest

from flow_fakes import git
from flow_harness import FakeChildren, NEW_EXPORT
from flow_web import FlowWeb
from web_harness import VALID

MARIA = dict(user="maria", name="Maria Lopez", email="maria@example.org")


@pytest.fixture(params=["/rosart", "/other"], ids=["base-rosart", "base-other"])
def fw(request, tmp_path):
    world = FlowWeb(tmp_path, base_path=request.param)
    yield world
    world.close()


def press_check(fw, **who):
    reply = fw.web.post("/check", csrf=VALID, **(who or MARIA))
    assert reply.status == 303, reply.body
    return int(reply.headers["location"].rsplit("/", 1)[1])


def run_check(fw):
    job_id = press_check(fw)
    fw.wait()
    return fw.flow.store.get(job_id)


# --- the US1 independent test ------------------------------------------------------------------------

def test_check_then_publish_end_to_end_names_the_person_posts_one_slack_line_and_commits_the_export(fw):
    web, flow = fw.web, fw.flow
    commits_before = int(git("--git-dir", str(flow.bare), "rev-list", "--count", "HEAD").strip())

    # the home page offers Check changes
    home = web.get("/", **MARIA)
    assert home.status == 200 and "Check changes" in home.body and "Publish by" not in home.body

    # Check changes: 303 to the job page, which ends "Check passed" with the plain-words summary
    reply = web.post("/check", csrf=VALID, **MARIA)
    assert reply.status == 303
    job_page = reply.headers["location"]
    assert re.fullmatch(re.escape(web.base) + r"/jobs/\d+", job_page)
    fw.wait()
    passed = web.raw("GET", job_page, web.identity(**MARIA))
    assert "Check passed: ready to publish until 21:00" in passed.body and "New Toner" in passed.body
    assert "<form" in passed.body and 'name="snapshot"' in passed.body

    # the home page now shows the publishable summary with its deadline and the check time
    home = web.get("/", **MARIA)
    assert "Publish by 21:00" in home.body and "content as of 20:00" in home.body and "New Toner" in home.body
    snapshot_id = re.search(r'name="snapshot" value="([^"]+)"', home.body).group(1)
    assert fw.slack.requests == [], "a passed check posts nothing"

    # Publish: 303, then "Live" with the invalidation ID and the URLs to spot-check
    reply = web.post("/publish", {"snapshot": snapshot_id}, csrf=VALID, **MARIA)
    assert reply.status == 303
    publish_page = reply.headers["location"]
    fw.wait()
    live = web.raw("GET", publish_page, web.identity(**MARIA))
    assert "Live" in live.body and "I2EXAMPLE" in live.body
    assert "https://rosart.com.au/products/toner/" in live.body

    job = flow.store.get(int(publish_page.rsplit("/", 1)[1]))
    assert job.state == "published" and job.person_user == "maria" and job.person_name == "Maria Lopez"
    assert len(fw.slack.requests) == 1, "exactly one Slack line"
    assert "published by Maria Lopez" in fw.slack.texts[0] and "I2EXAMPLE" in fw.slack.texts[0]
    assert job.notice_state == "posted"
    assert int(git("--git-dir", str(flow.bare), "rev-list", "--count", "HEAD").strip()) == commits_before + 1
    assert git("--git-dir", str(flow.bare), "log", "-1", "--format=%an").strip() == "Maria Lopez"


def test_the_home_page_lists_the_last_publish_and_the_newest_jobs_with_links(fw):
    web = fw.web
    run_check(fw)
    snapshot_id = fw.flow.snapshots.current_publishable().id
    publish_job = web.post("/publish", {"snapshot": snapshot_id}, csrf=VALID, **MARIA)
    fw.wait()

    home = web.get("/", **MARIA).body

    assert "Last publish" in home and "Maria Lopez" in home
    links = re.findall(r'href="([^"]*/jobs/\d+)"', home)
    assert publish_job.headers["location"] in links and len(links) >= 2
    assert len(links) <= 20


def test_the_home_page_links_back_to_the_portal_home(fw):
    home = fw.web.get("/", **MARIA).body

    assert 'href="/"' in home


def test_a_product_title_with_markup_is_escaped_on_the_pages(tmp_path):
    export = dict(NEW_EXPORT)
    export["export/shopify/products.json"] = json.dumps([{"id": "1", "handle": "toner", "title": "<script>alert(1)</script>"}])
    world = FlowWeb(tmp_path, live_export=export)
    try:
        job = run_check(world)
        home = world.web.get("/", **MARIA).body
        page = world.web.get(f"/jobs/{job.id}", **MARIA).body
    finally:
        world.close()

    for body in (home, page):
        assert "<script>alert(1)" not in body and "&lt;script&gt;alert(1)&lt;/script&gt;" in body


# --- refusals --------------------------------------------------------------------------------------

def test_publish_of_a_snapshot_that_cannot_be_published_is_a_410_page_and_starts_nothing(fw):
    web = fw.web
    older_id = run_check(fw).snapshot_id
    newer = run_check(fw)

    reply = web.post("/publish", {"snapshot": older_id}, csrf=VALID, **MARIA)

    assert reply.status == 410 and "can no longer be published" in reply.body and "Check changes" in reply.body
    assert newer.snapshot_id != older_id
    unknown = web.post("/publish", {"snapshot": "rosart-9-000000000000"}, csrf=VALID, **MARIA)
    empty = web.post("/publish", {}, csrf=VALID, **MARIA)
    assert unknown.status == 410 and empty.status == 410
    with fw.flow.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs WHERE kind = 'publish'").fetchone()[0] == 0
    assert not fw.flow.fake_aws.ran("cloudfront")


def test_an_unknown_job_is_a_404(fw):
    assert fw.web.get("/jobs/999", **MARIA).status == 404
    assert fw.web.get("/jobs/abc", **MARIA).status == 404


def test_a_failed_check_page_says_nothing_was_published_and_offers_no_publish_button(tmp_path):
    world = FlowWeb(tmp_path, children=FakeChildren(fail={("npm", "test"): (1, "FAIL fidelity\n")}))
    try:
        job = run_check(world)
        page = world.web.get(f"/jobs/{job.id}", **MARIA).body
    finally:
        world.close()

    assert job.state == "failed"
    assert "Check failed: nothing was published" in page and 'name="snapshot"' not in page


# --- a running job ---------------------------------------------------------------------------------

def test_a_running_job_page_refreshes_every_five_seconds_and_the_home_page_shows_it(tmp_path):
    entered, release = threading.Event(), threading.Event()

    def block(argv, cwd):
        if argv == ["npm", "run", "build"]:
            entered.set()
            assert release.wait(30)

    world = FlowWeb(tmp_path, children=FakeChildren(hook=block))
    try:
        job_id = press_check(world)
        assert entered.wait(30)
        page = world.web.get(f"/jobs/{job_id}", **MARIA).body
        home = world.web.get("/", **MARIA).body
        release.set()
        world.wait()
        done = world.web.get(f"/jobs/{job_id}", **MARIA).body
    finally:
        release.set()
        world.close()

    assert '<meta http-equiv="refresh" content="5">' in page and "Checking" in page
    assert "Checking" in home and "Maria Lopez" in home and "Check changes" not in home
    assert 'http-equiv="refresh"' not in done and "Check passed" in done


# --- the path prefix and the one portal link ---------------------------------------------------------

PORTAL_HOME = "/"          # the one URL a page may emit without the prefix (owner, 2026-11-08)


def test_every_url_on_every_page_has_the_prefix_except_the_portal_home_link(fw):
    web = fw.web
    pages = {"home-empty": web.get("/", **MARIA).body}
    passed = run_check(fw)
    pages["home-snapshot"] = web.get("/", **MARIA).body
    pages["job-passed"] = web.get(f"/jobs/{passed.id}", **MARIA).body
    snapshot_id = fw.flow.snapshots.current_publishable().id
    reply = web.post("/publish", {"snapshot": snapshot_id}, csrf=VALID, **MARIA)
    fw.wait()
    pages["job-published"] = web.raw("GET", reply.headers["location"], web.identity(**MARIA)).body
    pages["home-after"] = web.get("/", **MARIA).body
    pages["gone"] = web.post("/publish", {"snapshot": snapshot_id}, csrf=VALID, **MARIA).body
    pages["not-found"] = web.get("/jobs/999", **MARIA).body

    for name, body in pages.items():
        for url in web.urls_in(body):
            assert url == PORTAL_HOME or url.startswith(web.base + "/"), (name, url)
            assert "://" not in url and not url.startswith("//"), (name, url)
        actions = set(web.form_actions_in(body))
        for url in set(web.urls_in(body)) - {PORTAL_HOME}:
            expected = 405 if url in actions else 200      # a form action is a POST-only route: GET says 405
            assert web.raw("GET", url, web.identity(**MARIA)).status == expected, (name, url)

    assert 'href="/"' in pages["home-empty"], "the home page links back to the portal"
    assert not any(f'action="{PORTAL_HOME}"' in b or f'src="{PORTAL_HOME}"' in b for b in pages.values()), \
        "the exception is for a link only, never a form action or an asset"


def test_the_redirect_to_a_job_carries_the_prefix(fw):
    reply = fw.web.post("/check", csrf=VALID, **MARIA)

    assert reply.status == 303 and reply.headers["location"].startswith(fw.web.base + "/jobs/")
    fw.wait()
