"""The publish job end to end against fakes (spec 006 T063; plan "Publish job" steps 0 to 10; FR-007, FR-008, FR-012).

Seams: ``steps.start_publish`` (the start transaction with its publishable re-check) and
``steps.run_publish(job, deps)``, with every collaborator a fake (``flow_harness.Flow``).
"""
import json

import pytest

import publish
from fake_aws import INVALIDATION_ID
from flow_fakes import git
from flow_harness import Flow

PUBLISHED = ("published", "published-cache-pending")


@pytest.fixture
def flow(tmp_path):
    return Flow(tmp_path)


def real_syncs(flow):
    return [c for c in flow.fake_aws.ran("s3", "sync") if "--dryrun" not in c]


def nothing_uploaded(flow):
    return not real_syncs(flow) and not flow.fake_aws.marker_writes and not flow.fake_aws.ran("cloudfront")


# --- the happy path --------------------------------------------------------------------------------

def test_publish_runs_the_six_passes_writes_the_marker_then_invalidates_and_ends_published(flow):
    snapshot_id = flow.checked()

    job, outcome = flow.publish(snapshot_id)

    assert job.state == "published" and outcome.state == "published"
    assert job.passes_started == 6 and job.snapshot_id == snapshot_id and job.invalidation_id == INVALIDATION_ID
    commands = flow.fake_aws.commands
    last_pass = max(i for i, c in enumerate(commands) if c[1:3] == ["s3", "sync"] and "--dryrun" not in c)
    marker = next(i for i, c in enumerate(commands) if c[1:3] == ["s3api", "put-object"])
    invalidation = next(i for i, c in enumerate(commands) if c[1:3] == ["cloudfront", "create-invalidation"])
    assert last_pass < marker < invalidation, "pass 6, then the marker, then the invalidation"
    assert len(real_syncs(flow)) == 6
    assert flow.fake_aws.ran("cloudfront", "wait")


def test_publish_uploads_exactly_the_snapshot_dist_not_the_workspace(flow):
    snapshot_id = flow.checked()
    dist = str(flow.snapshots.folder(snapshot_id) / "dist")

    flow.publish(snapshot_id)

    assert real_syncs(flow) and all(c[3].startswith(dist) for c in real_syncs(flow))
    assert all(str(flow.workspace_path()) not in " ".join(c) for c in real_syncs(flow))
    assert len(flow.children.npm_calls) == 4, "no rebuild: only the check's four commands ran"


def test_the_marker_names_the_box_the_job_and_the_content_and_no_person(flow):
    snapshot_id = flow.checked()

    job, _ = flow.publish(snapshot_id)

    (marker,) = flow.fake_aws.marker_writes
    assert marker["publisher"] == "box" and marker["publish_id"] == f"box-{job.id}" and marker["site"] == "rosart"
    assert marker["content_hash"] == publish.dist_hash(flow.snapshots.folder(snapshot_id) / "dist")
    assert "denise" not in json.dumps(marker).lower()


def test_the_snapshot_is_marked_published_and_not_offered_again(flow):
    snapshot_id = flow.checked()

    job, _ = flow.publish(snapshot_id)

    snapshot = flow.snapshots.get(snapshot_id)
    assert snapshot.status == "published" and snapshot.published_by_job_id == job.id
    assert flow.snapshots.current_publishable() is None


def test_a_still_running_cache_refresh_ends_published_cache_pending(tmp_path):
    flow = Flow(tmp_path, aws={"wait_exit": 255})
    snapshot_id = flow.checked()

    job, outcome = flow.publish(snapshot_id)

    assert job.state == "published-cache-pending" and job.invalidation_id == INVALIDATION_ID
    assert flow.snapshots.get(snapshot_id).status == "published"


def test_the_job_summary_carries_the_counts_and_the_short_changes_line(flow):
    snapshot_id = flow.checked()
    plan = json.loads(flow.snapshots.get(snapshot_id).plan_json)

    job, _ = flow.publish(snapshot_id)

    summary = json.loads(job.summary_json)
    assert summary["uploaded"] == plan["upload_count"] and summary["deleted"] == len(plan["delete_keys"])
    assert summary["changes"] == "1 product changed"


def test_the_history_commit_holds_the_snapshot_export_and_names_the_person(flow):
    snapshot_id = flow.checked()
    before = git("--git-dir", str(flow.bare), "rev-list", "--count", "HEAD").strip()

    job, _ = flow.publish(snapshot_id)

    assert git("--git-dir", str(flow.bare), "rev-list", "--count", "HEAD").strip() == str(int(before) + 1)
    assert job.export_commit == git("--git-dir", str(flow.bare), "rev-parse", "HEAD").strip()
    assert job.export_commit_error is None
    message = git("--git-dir", str(flow.bare), "log", "-1", "--format=%an|%ae|%B")
    assert message.startswith("Denise Hill|denise@example.org|") and f"Job: {job.id}" in message
    assert f"Snapshot: {snapshot_id}" in message and f"Invalidation: {INVALIDATION_ID}" in message
    committed = git("--git-dir", str(flow.bare), "show", "HEAD:export/shopify/products.json")
    assert "New Toner" in committed


def test_the_final_state_is_written_last_after_the_history_commit(flow):
    snapshot_id = flow.checked()
    seen = {}
    real_finish = flow.store.finish

    def finish(job_id, state, **values):
        row = flow.store.get(job_id)
        seen.update(state=state, running=row.state, passes=row.passes_started, commit=values.get("export_commit"))
        return real_finish(job_id, state, **values)

    flow.store.finish = finish
    flow.publish(snapshot_id)

    assert seen == {"state": "published", "running": "running", "passes": 6, "commit": seen["commit"]}
    assert seen["commit"], "the history commit is made before the final state is written"


def test_a_history_commit_that_fails_does_not_change_a_good_publish(flow):
    snapshot_id = flow.checked()
    moved = flow.bare.with_name("export.git.away")

    def break_history(command):
        if command[1:3] == ["cloudfront", "wait"]:
            flow.bare.rename(moved)
        return False

    flow.fake_aws.fail = break_history
    job, outcome = flow.publish(snapshot_id)
    moved.rename(flow.bare)

    assert job.state == "published"
    assert job.export_commit is None and job.export_commit_error


# --- refused at the start --------------------------------------------------------------------------

def test_a_snapshot_superseded_by_a_newer_passed_check_is_refused_before_a_job_starts(flow):
    from publisher.steps import NotPublishable

    old = flow.checked()
    flow.checked()

    with pytest.raises(NotPublishable):
        flow.publish(old)

    assert flow.store.running() is None
    with flow.store.connection() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs WHERE kind = 'publish'").fetchone()[0] == 0
    assert nothing_uploaded(flow)


def test_a_snapshot_whose_window_has_passed_is_refused(flow):
    from publisher.steps import NotPublishable

    snapshot_id = flow.checked()
    flow.clock.advance(3600)

    with pytest.raises(NotPublishable):
        flow.publish(snapshot_id)


def test_a_published_snapshot_is_refused_a_second_time(flow):
    from publisher.steps import NotPublishable

    snapshot_id = flow.checked()
    flow.publish(snapshot_id)

    with pytest.raises(NotPublishable):
        flow.publish(snapshot_id)


def test_an_unknown_snapshot_is_refused(flow):
    from publisher.steps import NotPublishable

    with pytest.raises(NotPublishable):
        flow.publish("rosart-1-000000000000")


def test_a_running_job_refuses_the_start_and_names_the_running_job(flow):
    from publisher.jobs import JobAlreadyRunning

    snapshot_id = flow.checked()
    running = flow.start("check")

    with pytest.raises(JobAlreadyRunning) as raised:
        flow.publish(snapshot_id)

    assert raised.value.running.id == running.id


def test_a_failed_later_check_does_not_make_the_older_passed_check_unpublishable(flow):
    snapshot_id = flow.checked()
    flow.children.fail[("npm", "test")] = (1, "FAIL\n")
    failed, _ = flow.check()
    assert failed.state == "failed"

    job, _ = flow.publish(snapshot_id)

    assert job.state == "published"


# --- refused before pass 1: nothing is uploaded ------------------------------------------------------

def test_a_changed_snapshot_file_fails_the_hash_check_and_nothing_goes_to_aws(flow):
    snapshot_id = flow.checked()
    (flow.snapshots.folder(snapshot_id) / "dist" / "index.html").write_text("tampered")
    asked = flow.fake_aws.commands[:]

    job, _ = flow.publish(snapshot_id)

    assert job.state == "failed" and job.reason_code == "snapshot-hash" and job.passes_started == 0
    assert flow.fake_aws.commands == asked, "no AWS command was run for the tampered snapshot"


def test_an_unusable_deploy_config_fails_before_aws(flow):
    snapshot_id = flow.checked()
    (flow.image / "deploy.config.json").write_text(json.dumps({"profile": "rosart-deploy", "bucket": "REPLACE_ME",
                                                                "region": "ap-southeast-2", "distributionId": "E2ABCDEF123456"}))
    asked = flow.fake_aws.commands[:]

    job, _ = flow.publish(snapshot_id)

    assert job.state == "failed" and flow.fake_aws.commands == asked


def test_a_refused_aws_identity_uploads_nothing_and_the_snapshot_stays_publishable(flow):
    snapshot_id = flow.checked()
    flow.fake_aws.identity_arn = "arn:aws:iam::123456789012:user/someone-else"

    job, _ = flow.publish(snapshot_id)

    assert job.state == "failed" and job.reason_code == "aws-identity-mismatch"
    assert nothing_uploaded(flow) and flow.snapshots.publishable(snapshot_id)


def test_aws_refusing_the_credentials_at_the_identity_check_uploads_nothing_and_can_be_retried(flow):
    snapshot_id = flow.checked()
    flow.fake_aws.fail = lambda c: c[1:3] == ["sts", "get-caller-identity"]

    job, _ = flow.publish(snapshot_id)

    assert job.state == "failed" and job.reason_code == "aws-before-upload"
    assert nothing_uploaded(flow) and flow.snapshots.publishable(snapshot_id)


MARKER = {"site": "rosart", "publish_id": "pc-20261108T000000Z", "published_at": "2026-11-08T00:00:00Z",
          "content_hash": "b" * 64, "publisher": "pc"}


@pytest.mark.parametrize("seen_by_check,now", [
    (None, MARKER), (MARKER, None), (MARKER, {**MARKER, "publish_id": "pc-20261108T010000Z"})],
    ids=["appeared", "vanished", "changed"])
def test_a_changed_marker_refuses_the_publish_marks_the_snapshot_stale_and_uploads_nothing(tmp_path, seen_by_check, now):
    flow = Flow(tmp_path, aws={"marker": seen_by_check})
    snapshot_id = flow.checked()
    flow.fake_aws.marker = now

    job, _ = flow.publish(snapshot_id)

    assert job.state == "failed" and job.reason_code == "site-changed" and job.passes_started == 0
    assert nothing_uploaded(flow)
    assert flow.snapshots.get(snapshot_id).status == "stale" and not flow.snapshots.publishable(snapshot_id)


def test_an_unchanged_marker_lets_the_publish_through(tmp_path):
    flow = Flow(tmp_path, aws={"marker": MARKER})
    snapshot_id = flow.checked()

    job, _ = flow.publish(snapshot_id)

    assert job.state == "published"


def test_a_new_deletion_in_the_second_dry_run_refuses_the_publish(flow):
    snapshot_id = flow.checked()
    flow.fake_aws.extra_deletes = ("surprise/index.html",)

    job, _ = flow.publish(snapshot_id)

    assert job.state == "failed" and job.reason_code == "new-deletions" and job.passes_started == 0
    assert nothing_uploaded(flow)


def test_a_subset_of_the_stored_deletions_is_allowed(flow):
    snapshot_id = flow.checked()
    flow.fake_aws.no_deletes = True               # an earlier partial publish already removed them

    job, _ = flow.publish(snapshot_id)

    assert job.state == "published"


# --- part-way failures -----------------------------------------------------------------------------

def test_passes_started_is_written_before_each_pass_starts(flow):
    snapshot_id = flow.checked()
    seen = []

    def spy(command):
        if command[1:3] == ["s3", "sync"] and "--dryrun" not in command:
            seen.append(flow.store.running().passes_started)
        return False

    flow.fake_aws.fail = spy
    flow.publish(snapshot_id)

    assert seen == [1, 2, 3, 4, 5, 6]


@pytest.mark.parametrize("failing_pass", [1, 3, 6])
def test_a_failed_pass_ends_publish_failed_partial_with_no_marker_and_no_invalidation(flow, failing_pass):
    snapshot_id = flow.checked()
    flow.fake_aws.fail_passes = {failing_pass}

    job, _ = flow.publish(snapshot_id)

    assert job.state == "publish-failed-partial" and job.passes_started == failing_pass
    assert not flow.fake_aws.marker_writes and not flow.fake_aws.ran("cloudfront")
    assert flow.snapshots.get(snapshot_id).status == "kept" and flow.snapshots.publishable(snapshot_id)
    assert job.export_commit is None and job.export_commit_error is None, "no history commit for a partial publish"


def test_a_failed_marker_write_ends_publish_failed_partial_and_does_not_invalidate(flow):
    snapshot_id = flow.checked()
    flow.fake_aws.fail = lambda c: c[1:3] == ["s3api", "put-object"]

    job, _ = flow.publish(snapshot_id)

    assert job.state == "publish-failed-partial" and job.passes_started == 6
    assert not flow.fake_aws.ran("cloudfront")


def test_a_failed_invalidation_request_ends_publish_failed_partial(flow):
    snapshot_id = flow.checked()
    flow.fake_aws.fail = lambda c: c[1:3] == ["cloudfront", "create-invalidation"]

    job, _ = flow.publish(snapshot_id)

    assert job.state == "publish-failed-partial" and flow.fake_aws.marker_writes


def test_a_republish_after_a_partial_publish_runs_all_six_passes_and_invalidates(flow):
    snapshot_id = flow.checked()
    flow.fake_aws.fail_passes = {3}
    partial, _ = flow.publish(snapshot_id)
    assert partial.state == "publish-failed-partial"
    flow.fake_aws.fail_passes = set()
    before = len(real_syncs(flow))

    job, _ = flow.publish(snapshot_id)

    assert job.state == "published" and job.passes_started == 6
    again = real_syncs(flow)[before:]
    assert len(again) == 6 and sum("--delete" in c for c in again) == 5, "passes 2 to 6 run with --delete"
    assert flow.fake_aws.ran("cloudfront", "create-invalidation") and flow.fake_aws.marker_writes
