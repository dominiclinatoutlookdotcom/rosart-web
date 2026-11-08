"""History, job detail and the job log (spec 006 T077; FR-016, US4 scenarios 1 and 2, SC-003;
contracts/http-routes.md routes GET /jobs, GET /jobs/<id>, GET /jobs/<id>/log).

Seam: the real server over the whole fake world (``flow_web.FlowWeb``), under ``/rosart`` and ``/other``.
"""
import re

import pytest

from flow_harness import CODE_COMMIT, FakeChildren
from flow_web import FlowWeb
from web_harness import VALID

MARIA = dict(user="maria", name="Maria Lopez", email="maria@example.org")
JOHN = dict(user="john", name="John Smith", email="john@example.org")
EMAIL_OUTPUT = ("  - contact/index.html (page /contact/): email address info@deniva.com.au is not the site email "
                "info@rosart.com.au\n")


@pytest.fixture(params=["/rosart", "/other"], ids=["base-rosart", "base-other"])
def fw(request, tmp_path):
    world = FlowWeb(tmp_path, base_path=request.param, children=FakeChildren())
    yield world
    world.close()


def press(world, path, who, fields=None):
    reply = world.web.post(path, fields, csrf=VALID, **who)
    assert reply.status == 303, reply.body
    world.wait()
    return int(reply.headers["location"].rsplit("/", 1)[1])


@pytest.fixture()
def three_jobs(fw):
    """A passed check (Maria), a failed check (John) and a publish of the first (Maria)."""
    passed = press(fw, "/check", MARIA)
    snapshot_id = fw.flow.snapshots.current_publishable().id
    published = press(fw, "/publish", MARIA, {"snapshot": snapshot_id})
    fw.flow.children.fail = {("npm", "run", "check:dist"): (1, EMAIL_OUTPUT)}
    fw.flow.clock.advance(120)
    failed = press(fw, "/check", JOHN)
    return passed, published, failed


def rows_of(html):
    return re.findall(r"<tr>(.*?)</tr>", html, re.S)


# --- GET /jobs ----------------------------------------------------------------------------------------

def test_history_lists_every_job_newest_first_with_person_times_state_in_words_and_counts(fw, three_jobs):
    passed, published, failed = three_jobs

    page = fw.web.get("/jobs", **MARIA)

    assert page.status == 200
    rows = [row for row in rows_of(page.body) if "<td>" in row]
    assert [int(re.search(r"/jobs/(\d+)", row).group(1)) for row in rows] == [failed, published, passed]
    failed_row, published_row, passed_row = rows
    assert "John Smith" in failed_row and "Failed" in failed_row and re.search(r"8 Nov 20:0[2-9]", failed_row)
    assert "Maria Lopez" in published_row and "Live" in published_row and "1 uploaded" in published_row
    assert "Maria Lopez" in passed_row and "Check passed" in passed_row and "1 product changed" in passed_row
    assert "8 Nov 20:00" in passed_row, "times are shown in Melbourne time"
    assert page.headers["cache-control"] == "no-store"


def test_history_shows_fifty_jobs_a_page_and_page_two_has_the_rest(fw):
    store = fw.flow.store
    ids = []
    for number in range(55):
        job = store.start_job("check", "maria", "Maria Lopez", "m@example.org", CODE_COMMIT, 1200)
        store.finish(job.id, "failed", reason_code="unknown", reason_text="x", failures_json="[]")
        ids.append(job.id)

    first = fw.web.get("/jobs", **MARIA).body
    second = fw.web.get("/jobs?page=2", **MARIA).body
    garbage = fw.web.get("/jobs?page=abc", **MARIA)
    beyond = fw.web.get("/jobs?page=99", **MARIA)

    first_ids = [int(m) for m in re.findall(r"/jobs/(\d+)\"[^>]*>#", first)]
    second_ids = [int(m) for m in re.findall(r"/jobs/(\d+)\"[^>]*>#", second)]
    assert first_ids == ids[::-1][:50] and second_ids == ids[::-1][50:]
    assert f'href="{fw.web.base}/jobs?page=2"' in first and f'href="{fw.web.base}/jobs"' in second
    assert garbage.status == 200 and f"#{ids[-1]}" in garbage.body
    assert beyond.status == 200 and "No jobs on this page" in beyond.body


def test_every_url_on_the_history_pages_has_the_prefix_and_resolves(fw, three_jobs):
    web = fw.web
    for path in ("/jobs", "/jobs?page=2", f"/jobs/{three_jobs[0]}", f"/jobs/{three_jobs[2]}"):
        body = web.get(path, **MARIA).body
        for url in web.urls_in(body):
            assert url == "/" or url.startswith(web.base + "/"), (path, url)
            if url != "/":
                expected = 405 if url in set(web.form_actions_in(body)) else 200
                assert web.raw("GET", url, web.identity(**MARIA)).status == expected, (path, url)


def test_every_job_row_carries_the_person_from_the_sign_in_headers(fw, three_jobs):
    with fw.flow.store.connection() as db:
        people = {r["id"]: (r["person_user"], r["person_name"]) for r in db.execute("SELECT * FROM jobs")}

    assert people == {three_jobs[0]: ("maria", "Maria Lopez"), three_jobs[1]: ("maria", "Maria Lopez"),
                      three_jobs[2]: ("john", "John Smith")}


def test_history_needs_a_sign_in(fw):
    assert fw.web.get("/jobs", user=None).status == 403
    assert fw.web.get("/jobs/1/log", user=None).status == 403


# --- GET /jobs/<id> -----------------------------------------------------------------------------------

def test_a_published_job_shows_the_commit_the_snapshot_the_check_it_came_from_and_the_rest(fw, three_jobs):
    passed, published, _ = three_jobs
    job = fw.flow.store.get(published)

    page = fw.web.get(f"/jobs/{published}", **MARIA).body

    assert "Live" in page and "I2EXAMPLE" in page
    assert job.export_commit and job.export_commit[:12] in page
    assert job.snapshot_id in page
    assert re.search(rf'href="{re.escape(fw.web.base)}/jobs/{passed}"[^>]*>[^<]*check', page, re.I), \
        "the snapshot's check job is linked"
    assert CODE_COMMIT in page
    assert "Slack notice" in page and "posted" in page
    assert re.search(rf'href="{re.escape(fw.web.base)}/jobs/{published}/log"', page)
    assert "https://rosart.com.au/products/toner/" in page
    assert "New Toner" in page, "the change summary"


def test_a_failed_check_shows_the_failures_with_their_fix_and_its_log_link(fw, three_jobs):
    failed = three_jobs[2]

    page = fw.web.get(f"/jobs/{failed}", **MARIA).body

    assert "Check failed: nothing was published" in page
    assert "change the email to info@rosart.com.au in the Shopify admin" in page and "/contact/" in page
    assert "John Smith" in page and CODE_COMMIT in page
    assert f"/jobs/{failed}/log" in page


def test_a_pending_export_commit_and_a_failed_notice_are_stated_in_words(fw, three_jobs):
    published = three_jobs[1]
    fw.flow.store.update(published, notice_state="failed", export_commit_error="the history repository is read-only")

    page = fw.web.get(f"/jobs/{published}", **MARIA).body

    assert "could not be posted" in page
    assert "history commit failed" in page.lower() or "the history repository is read-only" in page


# --- GET /jobs/<id>/log -------------------------------------------------------------------------------

def test_the_log_is_the_scrubbed_job_log_as_plain_text(fw, three_jobs):
    published = three_jobs[1]
    job = fw.flow.store.get(published)
    on_disk = (fw.flow.config.data / job.log_path).read_text(encoding="utf-8")

    reply = fw.web.get(f"/jobs/{published}/log", **MARIA)

    assert reply.status == 200 and reply.headers["content-type"] == "text/plain; charset=utf-8"
    assert reply.body == on_disk and "== pass-6" in reply.body
    assert "client-secret-planted" not in reply.body and "xoxb-planted-slack" not in reply.body


def test_unknown_ids_and_missing_logs_are_404(fw, three_jobs):
    assert fw.web.get("/jobs/999/log", **MARIA).status == 404
    assert fw.web.get("/jobs/abc/log", **MARIA).status == 404
    assert fw.web.get("/jobs/999", **MARIA).status == 404
    job = fw.flow.store.get(three_jobs[0])
    (fw.flow.config.data / job.log_path).unlink()
    assert fw.web.get(f"/jobs/{job.id}/log", **MARIA).status == 404
