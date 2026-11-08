"""Job supervision: every child in its own process group, killed and awaited (spec 006 T043; E2, plan "Job supervision").

Seams: ``publisher.runner.ChildRunner.run`` and ``kill_and_wait``. The logic is covered on every platform
with fake process operations and plain children; the real process-group cases carry the ``posix`` marker
(run under WSL or ``make image-test``).
"""
import os
import signal
import subprocess
import sys
import time

import pytest

from fake_clock import FakeClock
from shopify_admin import Secret

TERM, KILL = 15, 9
PYTHON = sys.executable


# --- fake process operations: the kill-and-wait logic on any platform -------------------------

class FakeTime:
    def __init__(self):
        self.now = 0.0

    def mono(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


class FakeOps:
    """A process group that dies ``delay`` seconds after the signals in ``dies_on``."""

    def __init__(self, time_, dies_on=(TERM,), delay=0.0, start=None, alive=True):
        self.time = time_
        self.dies_on = set(dies_on)
        self.delay = delay
        self.start = start
        self.dead_at = None if alive else -1.0
        self.signals = []

    def killpg(self, pgid, sig):
        if self.dead_at is not None and self.time.now >= self.dead_at:
            raise ProcessLookupError(pgid)
        if sig == 0:
            return
        self.signals.append(sig)
        if sig in self.dies_on and self.dead_at is None:
            self.dead_at = self.time.now + self.delay

    def start_time(self, pid):
        return self.start


def kill(ops, time_, started=None, **kwargs):
    from publisher.runner import kill_and_wait

    kwargs.setdefault("proc_table", lambda: None)      # fake ops: the real /proc knows nothing of group 4242
    kill_and_wait(4242, started, ops=ops, sleep=time_.sleep, mono=time_.mono, **kwargs)


def test_a_group_that_ends_on_sigterm_gets_only_sigterm():
    t = FakeTime()
    ops = FakeOps(t, dies_on=(TERM,))

    kill(ops, t)

    assert ops.signals == [TERM]


def test_a_group_that_ignores_sigterm_gets_sigkill_after_the_grace_period():
    t = FakeTime()
    ops = FakeOps(t, dies_on=(KILL,))

    kill(ops, t, grace_s=5.0)

    assert ops.signals == [TERM, KILL]
    assert t.now >= 5.0


def test_it_returns_only_when_the_group_is_gone_not_when_the_signal_is_sent():
    t = FakeTime()
    ops = FakeOps(t, dies_on=(TERM,), delay=3.0)

    kill(ops, t)

    assert t.now >= 3.0 and ops.signals == [TERM]
    with pytest.raises(ProcessLookupError):
        ops.killpg(4242, 0)


def test_a_group_that_is_already_gone_is_not_an_error():
    t = FakeTime()
    ops = FakeOps(t, alive=False)

    kill(ops, t)

    assert ops.signals == []


def test_a_group_whose_only_members_are_zombies_counts_as_gone():
    """Without an init (pytest as PID 1) orphaned dead grandchildren stay zombies and killpg(pgid, 0) still succeeds."""
    t = FakeTime()
    ops = FakeOps(t, dies_on=())            # killpg(pgid, 0) never raises: the zombies keep the group "alive"
    table = lambda: [(4242, 4242, "Z"), (4300, 4242, "Z"), (9, 9, "S")]
    kill(ops, t, grace_s=1.0, kill_wait_s=1.0, proc_table=table)
    assert ops.signals == [TERM]


def test_a_group_with_a_live_member_among_zombies_is_still_stuck():
    from publisher.runner import ChildStuck

    t = FakeTime()
    ops = FakeOps(t, dies_on=())
    table = lambda: [(4242, 4242, "Z"), (4300, 4242, "S")]
    with pytest.raises(ChildStuck):
        kill(ops, t, grace_s=1.0, kill_wait_s=1.0, proc_table=table)


def test_without_a_process_table_the_signal_check_decides():
    from publisher.runner import ChildStuck

    t = FakeTime()
    ops = FakeOps(t, dies_on=())
    with pytest.raises(ChildStuck):
        kill(ops, t, grace_s=1.0, kill_wait_s=1.0, proc_table=lambda: None)


def test_parse_proc_stat_gives_pid_group_and_state_even_with_a_odd_name():
    from publisher.runner import parse_proc_stat

    assert parse_proc_stat("77 (a b) c) Z 1 4242 4242 0 -1") == (77, 4242, "Z")


def test_a_group_that_never_ends_raises_instead_of_hanging():
    from publisher.runner import ChildStuck

    t = FakeTime()
    ops = FakeOps(t, dies_on=())

    with pytest.raises(ChildStuck):
        kill(ops, t, grace_s=1.0, kill_wait_s=1.0)
    assert ops.signals == [TERM, KILL]


def test_a_leader_with_another_start_time_is_a_reused_pid_and_nothing_is_killed():
    t = FakeTime()
    ops = FakeOps(t, start=999_999)

    kill(ops, t, started=123_456)

    assert ops.signals == []


def test_a_matching_start_time_or_a_gone_leader_is_killed():
    t = FakeTime()
    same = FakeOps(t, start=123_456)
    kill(same, t, started=123_456)
    assert same.signals == [TERM]

    t2 = FakeTime()
    leader_gone = FakeOps(t2, start=None)        # the leader died, a grandchild keeps the group alive
    kill(leader_gone, t2, started=123_456)
    assert leader_gone.signals == [TERM]


def test_the_start_time_is_field_22_of_proc_stat_even_when_the_name_has_spaces_and_brackets():
    from publisher.runner import parse_start_time

    after_name = ["S"] + ["0"] * 18 + ["424242"] + ["0"] * 30        # fields 3.. ; starttime is the 20th of them
    stat = "1234 (my (odd) name) " + " ".join(after_name)

    assert parse_start_time(stat) == 424242


# --- ChildRunner on any platform --------------------------------------------------------------

@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def store(tmp_path, clock):
    from publisher.jobs import JobStore

    return JobStore(tmp_path / "publisher.sqlite3", site="rosart", clock=clock)


@pytest.fixture()
def job(store):
    return store.start_job("check", person_user="denise", person_name="Denise", person_email="",
                           code_commit="abc", timeout_s=60)


def base_env():
    env = {"PATH": os.environ.get("PATH", "")}
    if sys.platform == "win32":
        env["SYSTEMROOT"] = os.environ.get("SYSTEMROOT", "")
    return env


def make_runner(store, job, clock, scrubber=None):
    from publisher.runner import ChildRunner

    return ChildRunner(store, job.id, clock, scrubber=scrubber)


def test_run_returns_the_exit_code_and_the_output_and_does_not_raise_on_failure(store, job, clock):
    runner = make_runner(store, job, clock)

    done = runner.run([PYTHON, "-c", "import sys; print('out'); print('err', file=sys.stderr); sys.exit(3)"],
                      env=base_env(), cwd=None, timeout_mono=None)

    assert (done.returncode, done.stdout.strip(), done.stderr.strip()) == (3, "out", "err")


def test_the_child_gets_exactly_the_environment_and_folder_it_was_given(store, job, clock, tmp_path):
    runner = make_runner(store, job, clock)
    env = dict(base_env(), ONLY_THIS="yes")

    done = runner.run([PYTHON, "-c", "import os; print(os.getcwd()); print(sorted(k for k in os.environ if k.startswith('ONLY')))"],
                      env=env, cwd=tmp_path, timeout_mono=None)

    folder, names = done.stdout.strip().splitlines()
    assert os.path.samefile(folder, tmp_path) and names == "['ONLY_THIS']"


def test_child_output_is_scrubbed(store, job, clock):
    from publisher.scrub import Scrubber

    scrubber = Scrubber()
    scrubber.register(Secret("planted-secret-value"))
    runner = make_runner(store, job, clock, scrubber=scrubber)

    done = runner.run([PYTHON, "-c", "import sys; print('a planted-secret-value b'); print('planted-sec', file=sys.stderr); print('ret-value', file=sys.stderr)"],
                      env=base_env(), cwd=None, timeout_mono=None)

    assert "planted-secret-value" not in done.stdout and "[redacted]" in done.stdout
    assert "planted-sec" not in done.stderr


def test_a_child_past_its_deadline_is_stopped_and_the_runner_raises(store, job, clock):
    from publisher.runner import ChildTimeout

    runner = make_runner(store, job, clock)
    started = time.monotonic()

    with pytest.raises(ChildTimeout):
        runner.run([PYTHON, "-c", "import time; time.sleep(60)"], env=base_env(), cwd=None,
                   timeout_mono=clock.mono() + 0.5)

    assert time.monotonic() - started < 20
    assert store.get(job.id).child_pgid is None


# --- the AWS runner goes through the ChildRunner ---------------------------------------------

class FakeChildRunner:
    def __init__(self, result=None, error=None):
        from publisher.runner import Completed

        self.result = result or Completed(0, "{}", "")
        self.error = error
        self.calls = []

    def mono(self):
        return 100.0

    def run(self, argv, env, cwd, timeout_mono):
        self.calls.append({"argv": argv, "env": env, "cwd": cwd, "timeout_mono": timeout_mono})
        if self.error:
            raise self.error
        return self.result


def test_the_aws_runner_starts_its_child_through_the_child_runner(tmp_path):
    from publish import AwsResult
    from publisher.aws import ServiceAwsRunner

    fake = FakeChildRunner()
    runner = ServiceAwsRunner(tmp_path / "aws", fake, executable="/usr/bin/aws", timeout=30)

    result = runner(["aws", "sts", "get-caller-identity", "--profile", "rosart-deploy"])

    [call] = fake.calls
    assert call["argv"] == ["/usr/bin/aws", "sts", "get-caller-identity", "--profile", "rosart-deploy"]
    assert call["env"]["AWS_SHARED_CREDENTIALS_FILE"] == str(tmp_path / "aws" / "credentials")
    assert call["timeout_mono"] == 130.0
    assert result == AwsResult(0, "{}", "")


def test_the_aws_runner_can_take_its_environment_from_the_config(tmp_path):
    from publisher.aws import ServiceAwsRunner

    fake = FakeChildRunner()
    runner = ServiceAwsRunner(tmp_path / "aws", fake, executable="/usr/bin/aws", env=lambda: {"ONLY": "this"})

    runner(["aws", "sts", "get-caller-identity"])

    assert fake.calls[0]["env"] == {"ONLY": "this"}


def test_a_timed_out_aws_child_becomes_a_publish_error(tmp_path):
    from publish import PublishError
    from publisher.aws import ServiceAwsRunner
    from publisher.runner import ChildTimeout

    runner = ServiceAwsRunner(tmp_path / "aws", FakeChildRunner(error=ChildTimeout("slow")), executable="/usr/bin/aws",
                              timeout=1)

    with pytest.raises(PublishError, match="timed out"):
        runner(["aws", "s3", "sync", "dist", "s3://rosart-site"])


# --- real process groups (POSIX) ---------------------------------------------------------------

posix = pytest.mark.posix


@posix
def test_each_child_runs_as_the_leader_of_its_own_process_group(store, job, clock):
    runner = make_runner(store, job, clock)

    done = runner.run([PYTHON, "-c", "import os; print(os.getpid(), os.getpgid(0), os.getsid(0))"],
                      env=base_env(), cwd=None, timeout_mono=None)

    pid, pgid, sid = (int(x) for x in done.stdout.split())
    assert pid == pgid == sid and pgid != os.getpgrp()


@posix
def test_the_group_and_the_leaders_start_time_are_on_the_job_row_while_the_child_runs_and_cleared_after(store, job, clock, tmp_path):
    from publisher.runner import proc_start_time

    script = ("import sqlite3, os, sys; db = sqlite3.connect(sys.argv[1]); "
              "row = db.execute('SELECT child_pgid, child_started FROM jobs WHERE id = ?', (int(sys.argv[2]),)).fetchone(); "
              "print(os.getpid(), row[0], row[1])")
    runner = make_runner(store, job, clock)

    done = runner.run([PYTHON, "-c", script, str(store.db_path), str(job.id)], env=base_env(), cwd=None, timeout_mono=None)

    pid, recorded_pgid, recorded_start = (int(x) for x in done.stdout.split())
    assert recorded_pgid == pid and recorded_start > 0
    assert store.get(job.id).child_pgid is None and store.get(job.id).child_started is None


@posix
def test_the_start_time_of_a_live_process_can_be_read():
    from publisher.runner import proc_start_time

    assert isinstance(proc_start_time(os.getpid()), int)


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@posix
def test_a_grandchild_left_behind_by_a_finished_child_is_killed_before_run_returns(store, job, clock):
    runner = make_runner(store, job, clock)

    done = runner.run(["sh", "-c", "sleep 60 & echo $!"], env=base_env(), cwd=None, timeout_mono=None)

    grandchild = int(done.stdout.strip())
    assert not alive(grandchild)
    assert store.get(job.id).child_pgid is None


@posix
def test_a_timeout_leaves_no_process_of_the_group_behind(store, job, clock, tmp_path):
    from publisher.runner import ChildTimeout

    pid_file = tmp_path / "grandchild.pid"
    runner = make_runner(store, job, clock)

    with pytest.raises(ChildTimeout):
        runner.run(["sh", "-c", f"sleep 60 & echo $! > {pid_file}; wait"], env=base_env(), cwd=None,
                   timeout_mono=clock.mono() + 0.5)

    assert not alive(int(pid_file.read_text().strip()))
    assert store.get(job.id).child_pgid is None


@posix
def test_kill_and_wait_ends_a_real_group_and_spares_a_reused_pid():
    from publisher.runner import kill_and_wait, proc_start_time

    child = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        started = proc_start_time(child.pid)

        kill_and_wait(child.pid, started + 1, reap=child.poll)          # a different leader: not ours
        assert child.poll() is None

        kill_and_wait(child.pid, started, reap=child.poll)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()


@posix
def test_a_child_that_ignores_sigterm_is_killed_after_the_grace_period():
    from publisher.runner import kill_and_wait

    child = subprocess.Popen(["sh", "-c", "trap '' TERM; sleep 60"], start_new_session=True)
    time.sleep(0.3)
    try:
        begun = time.monotonic()
        kill_and_wait(child.pid, None, grace_s=0.5, reap=child.poll)

        assert child.poll() == -signal.SIGKILL and time.monotonic() - begun < 10
    finally:
        if child.poll() is None:
            child.kill()
            child.wait()
