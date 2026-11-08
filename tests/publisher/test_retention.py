"""Retention of job records and logs (spec 006 T083; A-12, data-model "Retention").

Seam: a finished job runs through ``steps.run_check`` and ``steps.run_publish`` (``flow_harness.Flow``): after it,
job rows and logs older than ``RETENTION_DAYS`` are gone; ``JobStore.purge`` is the same rule applied directly
(start-up recovery calls it, T090). Old rows are made by stepping the fake wall clock back.
"""
from datetime import timedelta

import pytest

from flow_harness import CODE_COMMIT, Flow

DAY = 86400


def old_job(flow, days_ago, state="passed", kind="check", with_log=True, **values):
    """A finished job that ended ``days_ago`` days before the fake clock's now, with a log file."""
    flow.clock.step_wall(-days_ago * DAY)
    try:
        job = flow.store.start_job(kind, "maria", "Maria Lopez", "maria@example.org", CODE_COMMIT, 1200)
        flow.store.finish(job.id, state, **values)
    finally:
        flow.clock.step_wall(days_ago * DAY)
    if with_log:
        path = flow.config.data / job.log_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"log of job {job.id}\n", encoding="utf-8")
    return job.id


def job_ids(flow):
    with flow.store.connection() as db:
        return [row["id"] for row in db.execute("SELECT id FROM jobs ORDER BY id")]


def log_exists(flow, job_id):
    return (flow.config.data / f"logs/rosart/{job_id}.log").exists()


@pytest.fixture()
def flow(tmp_path):
    return Flow(tmp_path)


def test_a_check_deletes_job_rows_and_logs_older_than_a_year_and_keeps_newer_ones(flow):
    ancient = old_job(flow, 400)
    recent = old_job(flow, 100)

    job, _ = flow.check()

    assert job.state == "passed"
    assert ancient not in job_ids(flow) and not log_exists(flow, ancient)
    assert recent in job_ids(flow) and log_exists(flow, recent)
    assert job.id in job_ids(flow)


def test_a_failed_check_and_a_publish_purge_too(flow):
    first_ancient = old_job(flow, 400)
    flow.children.fail = {("npm", "run", "build"): (1, "boom\n")}
    failed, _ = flow.check()
    assert failed.state == "failed" and first_ancient not in job_ids(flow)

    flow.children.fail = {}
    snapshot_id = flow.checked()
    second_ancient = old_job(flow, 500)
    published, _ = flow.publish(snapshot_id)

    assert published.state == "published" and second_ancient not in job_ids(flow)


def test_retention_follows_the_retention_days_setting(tmp_path):
    flow = Flow(tmp_path, extra_env={"RETENTION_DAYS": "30"})
    older = old_job(flow, 40)
    newer = old_job(flow, 20)

    flow.check()

    assert older not in job_ids(flow) and newer in job_ids(flow)


def test_a_running_job_is_never_deleted(flow):
    running = flow.store.start_job("check", "maria", "Maria Lopez", "m@example.org", CODE_COMMIT, 1200)
    flow.clock.step_wall(400 * DAY)

    flow.store.purge(365, flow.config.data)

    assert running.id in job_ids(flow)


def test_a_publish_whose_history_commit_is_still_pending_is_kept(flow):
    pending = old_job(flow, 400, state="published", kind="publish", export_commit_error="the repository is read-only")
    done = old_job(flow, 400, state="published", kind="publish", export_commit="abc123def456")

    flow.store.purge(365, flow.config.data)

    assert pending in job_ids(flow) and done not in job_ids(flow)


def test_the_rows_and_logs_of_old_deleted_snapshots_go_with_their_jobs_but_a_kept_snapshot_keeps_its_job(flow):
    snapshot_job = flow.checked()
    kept = flow.snapshots.current_publishable()
    check_job = flow.store.get(kept.check_job_id)
    flow.clock.step_wall(400 * DAY)

    flow.store.purge(365, flow.config.data)

    assert check_job.id in job_ids(flow), "a snapshot row still points at it (foreign key)"
    flow.snapshots.set_status(kept.id, "deleted")
    flow.store.purge(365, flow.config.data)
    assert check_job.id not in job_ids(flow)
    assert flow.snapshots.get(kept.id) is None


def test_purge_returns_how_many_jobs_it_deleted_and_is_idempotent(flow):
    old_job(flow, 400)
    old_job(flow, 401)

    assert flow.store.purge(365, flow.config.data) == 2
    assert flow.store.purge(365, flow.config.data) == 0
