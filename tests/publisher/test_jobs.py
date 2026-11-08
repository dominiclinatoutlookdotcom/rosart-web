"""The job store: SQLite in WAL mode, the states of FR-010, one final state (spec 006 T041; data-model "Job").

Seam: ``publisher.jobs.JobStore`` over a real SQLite file in a temp folder, with the fake clock.
"""
import logging
import sqlite3
from datetime import timedelta

import pytest

from fake_clock import FakeClock

JOB_COLUMNS = {"id", "site", "kind", "state", "phase", "passes_started", "person_user", "person_name",
               "person_email", "created_at", "started_at", "finished_at", "deadline_at", "child_pgid",
               "child_started", "snapshot_id", "reason_code", "reason_text", "failures_json", "summary_json",
               "invalidation_id", "export_commit", "export_commit_error", "code_commit", "notice_state", "log_path"}
SNAPSHOT_COLUMNS = {"id", "site", "check_job_id", "created_at", "window_end", "created_mono", "boot_id",
                    "marker_json", "content_hash", "plan_json", "summary_json", "status", "published_by_job_id"}
CHECK_FINALS = ["passed", "failed", "interrupted"]
PUBLISH_FINALS = ["published", "published-cache-pending", "failed", "publish-failed-partial", "interrupted"]


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def store(tmp_path, clock):
    from publisher.jobs import JobStore

    return JobStore(tmp_path / "publisher.sqlite3", site="rosart", clock=clock)


def start(store, kind="check", user="denise", **kwargs):
    return store.start_job(kind, person_user=user, person_name=kwargs.pop("name", "Denise Hill"),
                           person_email="denise@example.org", code_commit="abc1234", timeout_s=1200, **kwargs)


def test_the_database_is_in_wal_mode_with_the_documented_tables(store, tmp_path):
    with sqlite3.connect(tmp_path / "publisher.sqlite3") as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        jobs = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
        snapshots = {row[1] for row in db.execute("PRAGMA table_info(snapshots)")}
    assert JOB_COLUMNS <= jobs
    assert SNAPSHOT_COLUMNS <= snapshots


def test_reopening_an_existing_database_keeps_its_rows(store, tmp_path, clock):
    from publisher.jobs import JobStore

    job = start(store)
    reopened = JobStore(tmp_path / "publisher.sqlite3", site="rosart", clock=clock)

    assert reopened.get(job.id).person_user == "denise"


def test_a_started_job_is_running_with_its_person_site_and_times(store, clock):
    job = start(store, "publish")

    assert (job.site, job.kind, job.state) == ("rosart", "publish", "running")
    assert (job.person_user, job.person_name, job.person_email) == ("denise", "Denise Hill", "denise@example.org")
    assert job.code_commit == "abc1234" and job.notice_state == "not-required"
    assert job.started_at == clock.utc_now().isoformat() and job.finished_at is None
    assert job.deadline_at == (clock.utc_now() + timedelta(seconds=1200)).isoformat()
    assert job.log_path == f"logs/rosart/{job.id}.log"
    assert job.passes_started == 0 and job.child_pgid is None
    assert store.get(job.id) == job and store.running() == job


def test_job_ids_increase(store):
    first = start(store)
    store.finish(first.id, "passed")
    second = start(store)

    assert second.id > first.id


@pytest.mark.parametrize("user", ["", "   ", None])
def test_no_job_starts_without_a_person(store, user):
    with pytest.raises(ValueError, match="person"):
        store.start_job("check", person_user=user, person_name="", person_email="", code_commit="abc", timeout_s=10)
    assert store.running() is None


def test_the_database_itself_refuses_a_row_without_a_person_or_a_site(store, tmp_path):
    with sqlite3.connect(tmp_path / "publisher.sqlite3") as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO jobs (site, kind, state, person_user, code_commit, created_at) "
                       "VALUES ('rosart', 'check', 'passed', NULL, 'abc', 'now')")
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO jobs (site, kind, state, person_user, code_commit, created_at) "
                       "VALUES (NULL, 'check', 'passed', 'denise', 'abc', 'now')")


def test_only_the_states_of_fr010_exist(store, tmp_path):
    job = start(store)
    with sqlite3.connect(tmp_path / "publisher.sqlite3") as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("UPDATE jobs SET state = 'weird' WHERE id = ?", (job.id,))


@pytest.mark.parametrize("kind, state", [("check", s) for s in CHECK_FINALS] + [("publish", s) for s in PUBLISH_FINALS])
def test_each_kind_can_end_in_its_own_final_states(store, kind, state):
    job = start(store, kind)

    assert store.finish(job.id, state, reason_code="x" if state == "failed" else None) is True
    done = store.get(job.id)
    assert done.state == state and done.finished_at is not None


@pytest.mark.parametrize("kind, state", [("check", "published"), ("check", "publish-failed-partial"),
                                         ("publish", "passed"), ("check", "running"), ("check", "weird")])
def test_a_state_the_kind_cannot_end_in_is_refused_and_the_job_keeps_running(store, kind, state):
    job = start(store, kind)

    with pytest.raises(ValueError, match="state"):
        store.finish(job.id, state)
    assert store.get(job.id).state == "running"


def test_the_final_state_is_written_once_and_a_second_write_is_a_logged_no_op(store, caplog):
    job = start(store, "publish")
    assert store.finish(job.id, "published", invalidation_id="I123") is True

    with caplog.at_level(logging.WARNING, logger="rosart.jobs"):
        assert store.finish(job.id, "failed", reason_code="late") is False

    done = store.get(job.id)
    assert done.state == "published" and done.reason_code is None and done.invalidation_id == "I123"
    assert any(f"job {job.id}" in record.getMessage() and "already" in record.getMessage() for record in caplog.records)


def test_finish_stores_the_reason_the_failures_and_the_summary(store):
    job = start(store)

    store.finish(job.id, "failed", reason_code="build-failed", reason_text="The build failed",
                 failures_json='[{"code": "build-failed"}]', phase="build")

    done = store.get(job.id)
    assert (done.reason_code, done.reason_text, done.phase) == ("build-failed", "The build failed", "build")
    assert done.failures_json == '[{"code": "build-failed"}]'


def test_finish_refuses_a_field_it_does_not_own(store):
    job = start(store)

    with pytest.raises(ValueError, match="person_user"):
        store.finish(job.id, "passed", person_user="someone-else")


def test_progress_fields_can_be_updated_while_running(store):
    job = start(store, "publish")

    store.update(job.id, phase="pass-2", passes_started=2)

    got = store.get(job.id)
    assert (got.phase, got.passes_started) == ("pass-2", 2) and got.state == "running"


def test_a_second_job_is_refused_while_one_runs_and_names_the_running_one(store):
    from publisher.jobs import JobAlreadyRunning

    first = start(store, "publish", user="denise", name="Denise")
    with pytest.raises(JobAlreadyRunning) as caught:
        start(store, "check", user="sam", name="Sam")

    assert caught.value.running.id == first.id and caught.value.running.person_name == "Denise"
    store.finish(first.id, "published")
    assert start(store, "check", user="sam").state == "running"


def test_the_child_group_is_recorded_and_cleared_on_the_job_row(store):
    job = start(store)

    store.set_child(job.id, pgid=4321, started=987654)
    assert (store.get(job.id).child_pgid, store.get(job.id).child_started) == (4321, 987654)
    store.clear_child(job.id)
    assert (store.get(job.id).child_pgid, store.get(job.id).child_started) == (None, None)
