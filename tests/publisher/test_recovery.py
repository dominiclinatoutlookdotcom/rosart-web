"""Start-up recovery (spec 006 T089; FR-010, US6 scenarios 1, 2 and 4, E2).

Seam: ``publisher.recovery.recover``, run over the whole fake world (``flow_web.FlowWeb``) with fake process
operations: rows left ``running`` become ``interrupted`` after any leftover child group with a matching start time is
killed and gone; one Slack notice each; the lock is free; the pages say what to do; the orphan and workspace
cleanup and retention run too. The real-process version is ``test_process_restart.py`` (posix).
"""
import html

import pytest

from flow_harness import CODE_COMMIT
from flow_web import FlowWeb
from test_child_runner import FakeOps, FakeTime
from web_harness import VALID

MARIA = dict(user="maria", name="Maria Lopez", email="maria@example.org")
TERM = 15


@pytest.fixture(params=["/rosart", "/other"], ids=["base-rosart", "base-other"])
def fw(request, tmp_path):
    world = FlowWeb(tmp_path, base_path=request.param)
    yield world
    world.close()


def recover(fw, ops=None):
    from publisher.recovery import recover as run

    flow = fw.flow
    report = run(store=flow.store, snapshots=flow.snapshots, workspace=flow.workspace, config=flow.config,
                 notifier=fw.runner._notifier, ops=ops, log_for=fw.runner._job_log_line)
    for thread in report.notices:
        thread.join(30)
    return report


def leave_running(fw, kind="check", snapshot_id=None, passes=0, pgid=None, started=None):
    from publisher.steps import start_publish

    flow = fw.flow
    if kind == "publish":
        job = start_publish(flow.deps(), snapshot_id, "maria", "Maria Lopez", "maria@example.org")
    else:
        job = flow.store.start_job("check", "maria", "Maria Lopez", "maria@example.org", CODE_COMMIT, 1200)
    flow.store.update(job.id, passes_started=passes, phase=f"pass-{passes}" if passes else "build")
    if pgid is not None:
        flow.store.set_child(job.id, pgid, started)
    return job.id


def test_a_check_left_running_becomes_interrupted_the_lock_is_free_and_one_notice_is_posted(fw):
    job_id = leave_running(fw)

    report = recover(fw)

    job = fw.flow.store.get(job_id)
    assert report.interrupted == [job_id]
    assert job.state == "interrupted" and job.reason_code == "interrupted" and job.finished_at
    assert fw.flow.store.running() is None
    assert len(fw.slack.requests) == 1 and f"job {job_id} (check by Maria Lopez) was interrupted" in fw.slack.texts[0]
    assert job.notice_state == "posted"
    assert fw.flow.store.start_job("check", "john", "John", "j@example.org", CODE_COMMIT, 1200)


@pytest.mark.parametrize("passes", [0, 3])
def test_a_publish_left_running_becomes_interrupted_and_the_notice_says_the_site_may_be_partly_updated(fw, passes):
    snapshot_id = fw.flow.checked()
    job_id = leave_running(fw, "publish", snapshot_id, passes)

    recover(fw)

    job = fw.flow.store.get(job_id)
    assert job.state == "interrupted" and job.passes_started == passes
    assert "may be partly updated" in fw.slack.texts[0]


def test_a_second_start_up_changes_nothing_and_posts_nothing(fw):
    leave_running(fw)
    recover(fw)
    posted = len(fw.slack.requests)

    report = recover(fw)

    assert report.interrupted == [] and len(fw.slack.requests) == posted == 1


def test_a_leftover_child_group_with_a_matching_start_time_is_killed_before_the_row_is_marked(fw):
    job_id = leave_running(fw, pgid=4242, started=777)
    ops = FakeOps(FakeTime(), start=777)
    seen = {}
    real = ops.killpg

    def spying(pgid, sig):
        seen.setdefault("state_at_first_kill", fw.flow.store.get(job_id).state)
        return real(pgid, sig)

    ops.killpg = spying

    recover(fw, ops=ops)

    row = fw.flow.store.get(job_id)
    assert TERM in ops.signals and seen["state_at_first_kill"] == "running"
    assert row.state == "interrupted" and row.child_pgid is None and row.child_started is None


def test_a_reused_pid_is_never_killed_but_the_job_is_still_interrupted(fw):
    job_id = leave_running(fw, pgid=4242, started=777)
    ops = FakeOps(FakeTime(), start=999)

    recover(fw, ops=ops)

    row = fw.flow.store.get(job_id)
    assert ops.signals == [] and row.state == "interrupted" and row.child_pgid is None


def page(fw, job_id):
    return html.unescape(fw.web.get(f"/jobs/{job_id}", **MARIA).body)


def test_an_interrupted_publish_offers_the_same_snapshot_while_its_window_lasts(fw):
    snapshot_id = fw.flow.checked()
    job_id = leave_running(fw, "publish", snapshot_id, 3)
    recover(fw)

    body = page(fw, job_id)

    assert "the live site may be partly updated" in body.lower() and "press publish again" in body.lower()
    assert f'name="snapshot" value="{snapshot_id}"' in body
    again = fw.web.post("/publish", {"snapshot": snapshot_id}, csrf=VALID, **MARIA)
    fw.wait()
    assert again.status == 303
    publish = fw.flow.store.get(int(again.headers["location"].rsplit("/", 1)[1]))
    assert publish.state == "published" and publish.passes_started == 6, "all six passes run again"


def test_an_interrupted_publish_after_the_window_says_to_check_again_and_offers_no_publish(fw):
    snapshot_id = fw.flow.checked()
    job_id = leave_running(fw, "publish", snapshot_id, 3)
    recover(fw)
    fw.flow.clock.advance(3601)

    body = page(fw, job_id)

    assert "the live site may be partly updated" in body.lower() and "check again, then publish" in body.lower()
    assert 'name="snapshot"' not in body


def test_an_interrupted_check_offers_a_new_check_and_never_a_publish_of_a_half_written_export(fw):
    job_id = leave_running(fw)
    partial = fw.flow.snapshots.partial_folder(job_id)
    (partial / "dist").mkdir(parents=True)
    recover(fw)

    body = page(fw, job_id)
    home = fw.web.get("/", **MARIA).body

    assert "interrupted by a restart; press Check changes again" in body
    assert 'name="snapshot"' not in body and 'name="snapshot"' not in home and "Check changes" in home
    assert not partial.exists(), "the half-written snapshot folder is removed"


def test_start_up_also_removes_orphans_stale_workspaces_and_old_jobs_before_serving(fw):
    flow = fw.flow
    flow.checked()
    orphan = flow.snapshots.root / "rosart-99-orphan0000000"
    orphan.mkdir(parents=True)
    stale = flow.workspace.work_root / "rosart@oldcommit"
    stale.mkdir(parents=True)
    flow.clock.step_wall(-400 * 86400)
    old = flow.store.start_job("check", "maria", "Maria", "m@example.org", CODE_COMMIT, 60)
    flow.store.finish(old.id, "failed", reason_code="unknown", reason_text="x")
    flow.clock.step_wall(400 * 86400)

    recover(fw)

    assert not orphan.exists() and not stale.exists()
    assert flow.store.get(old.id) is None
    assert flow.workspace.current() is not None, "the current workspace stays"
