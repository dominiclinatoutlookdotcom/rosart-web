"""Checked snapshots: hash, publishable predicate, retention, orphans (spec 006 T057).

data-model "Checked snapshot", "Publishable", "Retention"; A-6 and FR-007 as corrected 2026-11-08; E3, E9.
Seam: ``publisher.snapshots.SnapshotStore`` over a real SQLite store and real folders, with a fake clock.
"""
import hashlib
import json
import re
from datetime import timedelta

import pytest

from fake_clock import FakeClock
from flow_fakes import write_tree

WINDOW = 3600
PLAN = {"upload_count": 2, "delete_keys": ["old/index.html"], "deleted_pages": ["/old/"]}
SUMMARY = {"text": "Store content changed since the last publish:", "nothing_new": False}


class Env:
    def __init__(self, tmp_path):
        from publisher.jobs import JobStore
        from publisher.snapshots import SnapshotStore

        self.clock = FakeClock()
        self.store = JobStore(tmp_path / "data" / "publisher.sqlite3", "rosart", self.clock)
        self.data = tmp_path / "data"
        self.snaps = SnapshotStore(self.store, self.data, "rosart", self.clock, WINDOW)

    def check(self, state="passed", files=None, plan=None, marker=None):
        """Run a (fake) check to the given final state; a passed one records its snapshot."""
        job = self.store.start_job("check", "denise", "Denise", "d@example.org", "abc1234", 1200)
        snapshot = None
        if state == "passed":
            partial = self.snaps.partial_folder(job.id)
            write_tree(partial, {"dist/index.html": f"home {job.id}", "dist/_astro/a.css": "css",
                                 "export/shopify/products.json": "[]", ".gitattributes": "* -text",
                                 **(files or {})})
            snapshot = self.snaps.record(job.id, plan or PLAN, SUMMARY, marker)
        self.store.finish(job.id, state, snapshot_id=snapshot.id if snapshot else None)
        return job, snapshot

    def publish(self, snapshot, state="published", export_commit="c0ffee", export_error=None):
        job = self.store.start_job("publish", "denise", "Denise", "d@example.org", "abc1234", 1200)
        self.store.finish(job.id, state, snapshot_id=snapshot.id, export_commit=export_commit,
                          export_commit_error=export_error)
        if state in ("published", "published-cache-pending"):
            self.snaps.set_status(snapshot.id, "published", published_by_job_id=job.id)
        return job


@pytest.fixture
def env(tmp_path):
    return Env(tmp_path)


def test_a_snapshot_id_is_site_job_and_twelve_hex_of_the_content_hash(env):
    job, snap = env.check()

    assert re.fullmatch(rf"rosart-{job.id}-[0-9a-f]{{12}}", snap.id)
    assert snap.id.endswith(snap.content_hash[:12])
    assert (env.snaps.folder(snap.id) / "dist" / "index.html").is_file()
    assert not env.snaps.partial_folder(job.id).exists()


def test_the_content_hash_covers_sorted_file_hash_lines_then_the_canonical_plan(env):
    _, snap = env.check()
    dist = env.snaps.folder(snap.id) / "dist"
    lines = "".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  {p.relative_to(dist).as_posix()}\n"
                    for p in sorted(dist.rglob("*"), key=lambda p: p.relative_to(dist).as_posix()) if p.is_file())
    canonical = json.dumps(PLAN, sort_keys=True, separators=(",", ":"))

    assert snap.content_hash == hashlib.sha256((lines + canonical).encode("utf-8")).hexdigest()
    assert json.loads(snap.plan_json) == PLAN


def test_the_hash_changes_with_a_file_or_with_the_plan_and_not_with_key_order(env):
    from publisher.snapshots import content_hash

    _, snap = env.check()
    dist = env.snaps.folder(snap.id) / "dist"
    base = content_hash(dist, PLAN)
    reordered = {k: PLAN[k] for k in reversed(list(PLAN))}

    assert content_hash(dist, reordered) == base
    assert content_hash(dist, {**PLAN, "upload_count": 3}) != base
    (dist / "index.html").write_text("tampered")
    assert content_hash(dist, PLAN) != base


def test_a_snapshot_row_keeps_the_marker_and_the_monotonic_time_and_boot_id(env):
    marker = json.dumps({"publisher": "pc"}, sort_keys=True)
    env.clock.advance(5)
    _, snap = env.check(marker=marker)
    _, absent = env.check(marker=None)

    assert snap.marker_json == marker and absent.marker_json is None
    assert snap.created_mono == env.clock.mono() and snap.boot_id == "boot-1" and snap.status == "kept"
    assert env.snaps.get(snap.id).content_hash == snap.content_hash
    assert env.snaps.folder(snap.id).joinpath("snapshot.json").is_file()


def test_publishable_table(env):
    _, snap = env.check()
    assert env.snaps.publishable(snap.id)

    env.clock.advance(WINDOW - 1)
    assert env.snaps.publishable(snap.id), "one second before the window ends"
    env.clock.advance(1)
    assert not env.snaps.publishable(snap.id), "the window is over at 3600 s"


def test_status_other_than_kept_is_not_publishable(env):
    for status in ("published", "stale", "deleted"):
        _, snap = env.check()
        env.snaps.set_status(snap.id, status)
        assert not env.snaps.publishable(snap.id), status


def test_only_a_later_passed_check_supersedes_a_snapshot(env):
    _, first = env.check()

    env.check(state="failed")
    env.check(state="interrupted")
    assert env.snaps.publishable(first.id), "a later failed or interrupted check does not supersede it (E3)"

    _, second = env.check()
    assert not env.snaps.publishable(first.id) and env.snaps.publishable(second.id)
    assert env.snaps.current_publishable().id == second.id


def test_a_running_job_makes_nothing_publishable(env):
    _, snap = env.check()
    env.store.start_job("check", "denise", "Denise", "d@example.org", "abc1234", 1200)

    assert not env.snaps.publishable(snap.id)
    assert env.snaps.current_publishable() is None


def test_a_wall_clock_step_changes_nothing(env):
    _, snap = env.check()

    env.clock.step_wall(2 * 3600)
    assert env.snaps.publishable(snap.id)
    env.clock.step_wall(-4 * 3600)
    assert env.snaps.publishable(snap.id)
    env.clock.advance(WINDOW)
    env.clock.step_wall(-WINDOW)
    assert not env.snaps.publishable(snap.id), "stepping the wall clock back does not extend the window"


def test_another_boot_id_is_not_publishable(env):
    _, snap = env.check()

    env.clock.reboot("boot-2")

    assert not env.snaps.publishable(snap.id)


def test_an_unknown_snapshot_is_not_publishable(env):
    assert not env.snaps.publishable("rosart-9-000000000000")
    assert env.snaps.current_publishable() is None


def test_a_failed_publish_leaves_the_snapshot_publishable(env):
    _, snap = env.check()

    env.publish(snap, state="publish-failed-partial", export_commit=None)

    assert env.snaps.publishable(snap.id), "A-6: a failed or partial publish keeps its snapshot publishable"


def test_a_published_snapshot_is_not_offered_again(env):
    _, snap = env.check()

    env.publish(snap)

    assert env.snaps.current_publishable() is None


def test_retention_keeps_the_newest_passed_the_last_published_and_any_pending_commit(env):
    _, s1 = env.check()
    env.publish(s1)                                           # old publish, committed: goes
    _, s2 = env.check()
    env.publish(s2, export_commit=None, export_error="git failed")   # pending export commit: stays
    _, s3 = env.check()
    env.publish(s3)                                           # the last published: stays
    _, s4 = env.check()                                       # superseded and never published: goes
    _, s5 = env.check()                                       # the newest passed check: stays

    deleted = env.snaps.retain()

    assert sorted(deleted) == sorted([s1.id, s4.id])
    for kept in (s2, s3, s5):
        assert env.snaps.folder(kept.id).is_dir() and env.snaps.get(kept.id).status in ("kept", "published")
    for gone in (s1, s4):
        assert not env.snaps.folder(gone.id).exists() and env.snaps.get(gone.id).status == "deleted"


def test_start_up_removes_orphan_and_partial_folders_and_keeps_referenced_ones(env):
    _, snap = env.check()
    orphan = env.data / "snapshots" / "rosart" / "rosart-99-abcdefabcdef"
    partial = env.data / "snapshots" / "rosart" / "7.partial"
    write_tree(orphan, {"dist/index.html": "x"})
    write_tree(partial, {"dist/index.html": "x"})

    env.snaps.cleanup_orphans()

    assert not orphan.exists() and not partial.exists()
    assert env.snaps.folder(snap.id).is_dir()


def test_a_deleted_snapshots_leftover_folder_is_removed_at_start_up(env):
    _, snap = env.check()
    env.snaps.set_status(snap.id, "deleted")

    env.snaps.cleanup_orphans()

    assert not env.snaps.folder(snap.id).exists()


def test_the_window_end_is_display_only_and_one_hour_after_the_check(env):
    _, snap = env.check()

    from datetime import datetime

    created = datetime.fromisoformat(snap.created_at)
    assert datetime.fromisoformat(snap.window_end) - created == timedelta(seconds=WINDOW)
