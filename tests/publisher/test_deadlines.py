"""Job deadlines on the monotonic clock (spec 006 T091; FR-011, A-10 as corrected 2026-11-08, E7, E9).

Seams: ``steps.run_check`` over a real ``ChildRunner`` and a real long-sleeping child (the check deadline kills it
with its process group), and ``steps.run_publish`` over the fake AWS and a fake monotonic clock (the publish deadline
is checked only between passes; a pass that is running is never killed). ``FakeClock.step_wall`` proves the wall
clock decides nothing.
"""
import os
import sys

import pytest

from flow_harness import FakeChildren, Flow

PUBLISH_TIMEOUT = 100


class SleepingBuild:
    """Children whose ``npm run build`` is a real child that sleeps for two minutes (and records its pid)."""

    def __init__(self, flow, pid_file):
        self.flow, self.pid_file = flow, pid_file
        self.fake = FakeChildren(mono=flow.clock.mono)
        self.runner = None

    def mono(self):
        return self.flow.clock.mono()

    def run(self, argv, env, cwd, timeout_mono):
        from publisher.runner import ChildRunner

        if tuple(argv) != ("npm", "run", "build"):
            return self.fake.run(argv, env, cwd, timeout_mono)
        self.runner = ChildRunner(self.flow.store, self.flow.store.running().id, self.flow.clock)
        code = f"import os, time; open({str(self.pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(120)"
        return self.runner.run([sys.executable, "-c", code], dict(os.environ), cwd, timeout_mono)


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def sleeping_flow(tmp_path):
    flow = Flow(tmp_path, extra_env={"CHECK_TIMEOUT_S": "2"})
    flow.children = SleepingBuild(flow, tmp_path / "child.pid")
    return flow


def test_a_check_step_past_the_deadline_is_killed_and_the_job_fails_with_deadline(tmp_path):
    flow = sleeping_flow(tmp_path)

    job, _ = flow.check()

    assert job.state == "failed" and job.reason_code == "deadline"
    assert "took too long" in job.reason_text and job.child_pgid is None and job.child_started is None
    assert (tmp_path / "child.pid").is_file(), "the long child really started"


@pytest.mark.posix
def test_the_deadline_failure_is_written_only_after_the_child_is_gone(tmp_path):
    flow = sleeping_flow(tmp_path)
    seen = {}
    real_finish = flow.store.finish

    def finish(job_id, state, **values):
        pid = int((tmp_path / "child.pid").read_text())
        seen["alive_at_finish"] = alive(pid)
        return real_finish(job_id, state, **values)

    flow.store.finish = finish

    job, _ = flow.check()

    assert job.state == "failed" and seen["alive_at_finish"] is False


# --- the publish deadline is checked only between passes ---------------------------------------------

class Passes:
    """Counts the real sync passes the fake AWS runs and runs ``on_pass[n]`` while pass n is running."""

    def __init__(self, on_pass):
        self.on_pass, self.count = on_pass, 0

    def __call__(self, command):
        if command[1:3] == ["s3", "sync"] and "--dryrun" not in command:
            self.count += 1
            action = self.on_pass.get(self.count)
            if action:
                action()
        return False


def publishing(tmp_path, on_pass=None, on_identity=None, **env):
    box = {}
    passes = Passes({})

    def fail(command):
        if on_identity and command[1:3] == ["sts", "get-caller-identity"] and not box.get("identity_done"):
            box["identity_done"] = True
            on_identity()
        return passes(command)

    flow = Flow(tmp_path, aws={"fail": fail}, extra_env={"PUBLISH_TIMEOUT_S": str(PUBLISH_TIMEOUT), **env})
    box["flow"] = flow
    for number, make in (on_pass or {}).items():
        passes.on_pass[number] = make(flow)
    return flow


def test_a_pass_running_past_the_deadline_completes_and_the_next_pass_does_not_start(tmp_path):
    flow = publishing(tmp_path, on_pass={2: lambda f: lambda: f.clock.advance(PUBLISH_TIMEOUT + 50)})
    snapshot = flow.checked()

    job, _ = flow.publish(snapshot)

    assert job.state == "publish-failed-partial" and job.reason_code == "deadline"
    assert job.passes_started == 2 and flow.fake_aws.real_syncs == 2, "pass 2 completed, pass 3 never started"
    assert flow.fake_aws.marker_writes == [] and not flow.fake_aws.ran("cloudfront")


def test_past_the_deadline_before_pass_one_the_publish_fails_with_nothing_uploaded(tmp_path):
    clock_holder = {}
    flow = publishing(tmp_path, on_identity=lambda: clock_holder["flow"].clock.advance(PUBLISH_TIMEOUT + 1))
    clock_holder["flow"] = flow
    snapshot = flow.checked()

    job, _ = flow.publish(snapshot)

    assert job.state == "failed" and job.reason_code == "deadline" and job.passes_started == 0
    assert flow.fake_aws.real_syncs == 0 and flow.fake_aws.marker_writes == []


def test_past_the_deadline_after_pass_six_the_marker_and_the_invalidation_still_run_with_no_wait(tmp_path):
    flow = publishing(tmp_path, on_pass={6: lambda f: lambda: f.clock.advance(PUBLISH_TIMEOUT + 50)})
    snapshot = flow.checked()

    job, _ = flow.publish(snapshot)

    assert job.state == "published-cache-pending" and job.invalidation_id
    assert len(flow.fake_aws.marker_writes) == 1 and flow.fake_aws.ran("cloudfront", "create-invalidation")
    assert not flow.fake_aws.ran("cloudfront", "wait"), "no time left, so no wait"
    assert flow.snapshots.get(snapshot).status == "published"


def test_the_invalidation_wait_is_never_longer_than_the_time_left(tmp_path):
    flow = publishing(tmp_path, on_pass={6: lambda f: lambda: f.clock.advance(PUBLISH_TIMEOUT - 40)},
                      INVALIDATION_WAIT_S="600")
    timeouts = []
    real_make = flow.make_aws

    def make_aws(config, temp_dir, timeout_s=None):
        timeouts.append(timeout_s)
        return real_make(config, temp_dir, timeout_s)

    flow.make_aws = make_aws
    snapshot = flow.checked()
    timeouts.clear()

    job, _ = flow.publish(snapshot)

    waits = [t for t in timeouts if t is not None]
    assert job.state == "published" and waits == [40.0]


def test_the_full_wait_is_used_when_there_is_plenty_of_time(tmp_path):
    flow = publishing(tmp_path, INVALIDATION_WAIT_S="30")
    timeouts = []
    real_make = flow.make_aws

    def make_aws(config, temp_dir, timeout_s=None):
        timeouts.append(timeout_s)
        return real_make(config, temp_dir, timeout_s)

    flow.make_aws = make_aws
    snapshot = flow.checked()
    timeouts.clear()

    flow.publish(snapshot)

    assert [t for t in timeouts if t is not None] == [30.0]


def test_a_wall_clock_step_in_the_middle_changes_nothing(tmp_path):
    flow = publishing(tmp_path, on_pass={2: lambda f: lambda: f.clock.step_wall(2 * 3600),
                                         4: lambda f: lambda: f.clock.step_wall(-4 * 3600)})
    snapshot = flow.checked()

    job, _ = flow.publish(snapshot)

    assert job.state == "published" and job.passes_started == 6
