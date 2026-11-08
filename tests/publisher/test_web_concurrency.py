"""One job at a time across Check and Publish, with an immediate message naming the running job
(spec 006 T075; FR-009, US3 scenarios 1 to 3).

Seam: the real server over the whole fake world (``flow_web.FlowWeb``); a hook in the fake ``npm`` holds a check
open so a second press arrives while it runs. Run under ``/rosart`` and ``/other``.
"""
import re
import threading

import pytest

from flow_harness import FakeChildren
from flow_web import FlowWeb
from web_harness import VALID

MARIA = dict(user="maria", name="Maria Lopez", email="maria@example.org")
JOHN = dict(user="john", name="John Smith", email="john@example.org")


class Gate:
    """Holds the fake build of a check until the test releases it."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()
        self.active = True

    def hook(self, argv, cwd):
        if self.active and argv == ["npm", "run", "build"]:
            self.entered.set()
            assert self.release.wait(60)

    def open(self):
        self.active = False
        self.release.set()


@pytest.fixture(params=["/rosart", "/other"], ids=["base-rosart", "base-other"])
def gated(request, tmp_path):
    gate = Gate()
    world = FlowWeb(tmp_path, base_path=request.param, children=FakeChildren(hook=gate.hook))
    yield world, gate
    gate.open()
    world.close()


def press(world, path, who, fields=None):
    return world.web.post(path, fields, csrf=VALID, **who)


def posted_together(world, requests):
    """Send ``requests`` (path, who, fields) at the same moment; returns the replies in order."""
    barrier = threading.Barrier(len(requests))
    replies = [None] * len(requests)

    def send(index, path, who, fields):
        barrier.wait()
        replies[index] = press(world, path, who, fields)

    threads = [threading.Thread(target=send, args=(i, *request)) for i, request in enumerate(requests)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    return replies


def job_rows(world):
    with world.flow.store.connection() as db:
        return db.execute("SELECT id, kind, state FROM jobs ORDER BY id").fetchall()


def test_two_checks_at_the_same_moment_start_exactly_one_job_and_the_other_is_told_who_is_running(gated):
    world, gate = gated

    first, second = posted_together(world, [("/check", MARIA, None), ("/check", JOHN, None)])
    assert gate.entered.wait(30)

    statuses = sorted([first.status, second.status])
    refused = first if first.status == 409 else second
    started_by = "Maria Lopez" if first.status == 303 else "John Smith"
    assert statuses == [303, 409], (first.body, second.body)
    assert len(job_rows(world)) == 1
    body = refused.body
    assert re.search(rf"a check started by {started_by} at \d\d:\d\d is running; wait for it to finish", body, re.I)
    assert f'href="{world.web.base}/jobs/{job_rows(world)[0]["id"]}"' in body
    assert f'href="{world.web.base}/"' in body, "the refusal page links back to the publisher"


def test_a_check_pressed_while_a_check_runs_gets_409_and_the_running_job_is_unaffected(gated):
    world, gate = gated
    started = press(world, "/check", MARIA)
    assert started.status == 303 and gate.entered.wait(30)
    job_id = int(started.headers["location"].rsplit("/", 1)[1])
    row_before = world.flow.store.get(job_id)
    log_path = world.flow.config.data / row_before.log_path
    log_before = log_path.read_text(encoding="utf-8")

    refused = press(world, "/check", JOHN)

    row_after = world.flow.store.get(job_id)
    assert refused.status == 409 and "John Smith" not in refused.body and "Maria Lopez" in refused.body
    assert (row_after.state, row_after.person_user, row_after.started_at, row_after.phase) == \
        ("running", "maria", row_before.started_at, row_before.phase)
    assert log_path.read_text(encoding="utf-8") == log_before, "a refused press never touches the running log"
    assert len(job_rows(world)) == 1
    gate.open()
    world.wait()
    assert world.flow.store.get(job_id).state == "passed"


def test_a_publish_pressed_while_a_check_runs_gets_409_and_uploads_nothing(gated):
    world, gate = gated
    gate.active = False
    first = press(world, "/check", MARIA)
    world.wait()
    snapshot_id = world.flow.snapshots.current_publishable().id
    gate.active = True
    gate.entered.clear()
    gate.release.clear()
    second = press(world, "/check", MARIA)
    assert second.status == 303 and gate.entered.wait(30)

    refused = press(world, "/publish", JOHN, {"snapshot": snapshot_id})

    assert first.status == 303 and refused.status == 409
    assert re.search(r"a check started by Maria Lopez at \d\d:\d\d is running; wait for it to finish", refused.body, re.I)
    assert [row["kind"] for row in job_rows(world)] == ["check", "check"]
    assert not world.flow.fake_aws.ran("cloudfront") and world.flow.fake_aws.real_syncs == 0


@pytest.fixture(params=["/rosart", "/other"], ids=["base-rosart", "base-other"])
def holding_aws(request, tmp_path):
    """A world whose first real ``aws s3 sync`` of a publish blocks until the test releases it."""
    entered, release = threading.Event(), threading.Event()

    def hold(command):
        if command[1:3] == ["s3", "sync"] and "--dryrun" not in command and not release.is_set():
            entered.set()
            assert release.wait(60)
        return False

    world = FlowWeb(tmp_path, base_path=request.param, aws={"fail": hold})
    yield world, entered, release
    release.set()
    world.close()


def test_a_double_submitted_publish_form_runs_one_publish_and_the_other_press_is_told_it_is_running(holding_aws):
    world, entered, release = holding_aws
    press(world, "/check", MARIA)
    world.wait()
    snapshot_id = world.flow.snapshots.current_publishable().id

    first, second = posted_together(world, [("/publish", MARIA, {"snapshot": snapshot_id}),
                                            ("/publish", MARIA, {"snapshot": snapshot_id})])
    assert entered.wait(30)

    assert sorted([first.status, second.status]) == [303, 409], (first.body, second.body)
    refused = first if first.status == 409 else second
    assert re.search(r"a publish started by Maria Lopez at \d\d:\d\d is running; wait for it to finish", refused.body,
                     re.I)
    release.set()
    world.wait()
    publishes = [row for row in job_rows(world) if row["kind"] == "publish"]
    assert len(publishes) == 1 and publishes[0]["state"] == "published"
    assert len(world.flow.fake_aws.ran("cloudfront", "create-invalidation")) == 1
    assert len(world.slack.requests) == 1
