"""Credentials that expire or are revoked (spec 006 T085; US5 scenarios 1 to 3, FR-010).

Seam: the real server over the whole fake world (``flow_web.FlowWeb``): the fake Shopify refuses the grant, the fake
AWS answers ``InvalidClientTokenId`` at the identity check or at a sync pass. Run under ``/rosart`` and ``/other``.
"""
import html

import pytest

from flow_fakes import git
from flow_web import FlowWeb
from web_harness import VALID

MARIA = dict(user="maria", name="Maria Lopez", email="maria@example.org")
INVALID_KEY = "An error occurred (InvalidClientTokenId) when calling the operation: The security token is invalid."


def make_world(request, tmp_path, **aws):
    return FlowWeb(tmp_path, base_path=request.param, aws=aws or None)


@pytest.fixture(params=["/rosart", "/other"], ids=["base-rosart", "base-other"])
def world(request, tmp_path):
    fw = make_world(request, tmp_path)
    yield fw
    fw.close()


def press(fw, path, fields=None):
    reply = fw.web.post(path, fields, csrf=VALID, **MARIA)
    assert reply.status == 303, reply.body
    fw.wait()
    job_id = int(reply.headers["location"].rsplit("/", 1)[1])
    return fw.flow.store.get(job_id)


def page(fw, job):
    return html.unescape(fw.web.get(f"/jobs/{job.id}", **MARIA).body)


# --- Shopify ----------------------------------------------------------------------------------------

@pytest.mark.parametrize("grant_error, code", [("shop_not_permitted", "shopify-not-permitted"),
                                               ("invalid_client", "shopify-access")])
def test_a_refused_shopify_credential_stops_the_check_before_anything_is_written(world, grant_error, code):
    flow = world.flow
    flow.shopify.grant_error = grant_error
    commits = git("--git-dir", str(flow.bare), "rev-list", "--count", "HEAD")

    job = press(world, "/check")

    body = page(world, job)
    assert job.state == "failed" and job.reason_code == code
    assert "the website's shopify access is not working: contact the owner" in body.lower()
    assert flow.export_calls == [] and flow.theme_calls == [], "nothing was read or written"
    with flow.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0
    assert git("--git-dir", str(flow.bare), "rev-list", "--count", "HEAD") == commits
    assert 'name="snapshot"' not in body
    assert len(world.slack.requests) == 1 and f"job {job.id} check failed for Maria Lopez" in world.slack.texts[0]
    assert "Shopify access is not working" in world.slack.texts[0]


# --- AWS --------------------------------------------------------------------------------------------

@pytest.fixture(params=["/rosart", "/other"], ids=["base-rosart", "base-other"])
def aws_world(request, tmp_path):
    switch = {"identity_fails": False}

    def fail(command):
        return switch["identity_fails"] and command[1:3] == ["sts", "get-caller-identity"]

    fw = make_world(request, tmp_path, fail=fail, error_text=INVALID_KEY)
    fw.switch = switch
    yield fw
    fw.close()


def test_a_refused_aws_key_at_the_identity_check_uploads_nothing_and_the_snapshot_stays_publishable(aws_world):
    fw = aws_world
    check = press(fw, "/check")
    snapshot_id = check.snapshot_id
    fw.switch["identity_fails"] = True

    job = press(fw, "/publish", {"snapshot": snapshot_id})

    body = page(fw, job)
    assert job.state == "failed" and job.reason_code == "aws-before-upload" and job.passes_started == 0
    assert "nothing was uploaded" in body and "AWS access is not working" in body
    assert fw.flow.fake_aws.real_syncs == 0 and not fw.flow.fake_aws.ran("cloudfront")
    assert fw.flow.snapshots.publishable(snapshot_id), "a retry inside the window is possible"
    assert f'name="snapshot" value="{snapshot_id}"' in body, "Publish is offered again"
    assert "stopped before upload" in fw.slack.texts[-1]
    fw.switch["identity_fails"] = False
    retry = press(fw, "/publish", {"snapshot": snapshot_id})
    assert retry.state == "published"


@pytest.mark.parametrize("failing_pass", [1, 3])
def test_a_refused_aws_key_during_a_pass_ends_partial_never_as_a_success(tmp_path, failing_pass):
    fw = FlowWeb(tmp_path, aws={"fail_passes": {failing_pass}, "error_text": INVALID_KEY})
    try:
        check = press(fw, "/check")
        job = press(fw, "/publish", {"snapshot": check.snapshot_id})
        body = page(fw, job)
        home = fw.web.get("/", **MARIA).body
        status = fw.web.get("/status.json", user=None).json()
    finally:
        fw.close()

    assert job.state == "publish-failed-partial" and job.reason_code == "partial-sync"
    assert job.passes_started == failing_pass
    assert "the live site may be partly updated" in body.lower()
    assert "a new publish will mirror the site fully" in body.lower()
    assert "Live" not in body.replace("live site", "") and "Invalidation ID" not in body
    assert "Publish failed" in home
    assert status["needs_human"] is True and status["last_publish"]["result"] == "failed"
    text = fw.slack.texts[-1]
    assert "FAILED part-way" in text and "partly updated" in text and "mirrors it fully" in text
    assert "published by" not in text
