"""The real service process across restarts, signals and worker death (spec 006 T093; plan "Restart mid-job" and
"Worker-thread death", US6 scenario 3, E2). POSIX only: run under WSL or the image test stage.

The service is ``restart_service.py`` (real runner, process groups, watchdog, recovery, SIGTERM handler, HTTP server
on loopback) over a ``Flow`` world; ``bin/aws`` blocks on a chosen pass, ignores SIGTERM and appends to a file every
100 ms, ``bin/npm`` runs a build until killed. Everything stays on 127.0.0.1.
"""
import http.client
import json
import os
import re
import signal
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

import pytest

from fake_clock import FakeClock
from flow_harness import Flow
from publisher.jobs import JobStore

pytestmark = pytest.mark.posix

HERE = Path(__file__).resolve().parent
SERVICE = HERE / "restart_service.py"
ORIGIN = "https://int.solagratia.com.au"
WHO = {"Remote-User": "maria", "Remote-Name": "Maria Lopez", "Remote-Email": "maria@example.org",
       "Remote-Groups": "rosart-publishers"}


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        return Path(f"/proc/{pid}/stat").read_text().split(")")[1].split()[0] != "Z"
    except OSError:
        return False


def wait_for(condition, seconds, what):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = condition()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError(f"timed out waiting for {what}")


class Service:
    def __init__(self, root, scenario=None, env=None):
        self.root = root
        self.env = {**os.environ, "PUBLISHER_REQUIRE_POSIX": "1", **(env or {})}
        if scenario is not None:
            path = root / "scenario.json"
            path.write_text(json.dumps(scenario), encoding="utf-8")
            self.env["FAKE_AWS_SCENARIO"] = str(path)
        self.proc = None
        self.port = None
        self.store = JobStore(root / "data" / "publisher.sqlite3", "rosart", FakeClock())

    def start(self):
        (self.root / "port").unlink(missing_ok=True)
        if getattr(self, "log", None) is not None:
            self.log.close()
        self.log = open(self.root / "service.log", "ab")
        self.proc = subprocess.Popen([sys.executable, str(SERVICE), str(self.root)], env=self.env, stdout=self.log,
                                     stderr=self.log, stdin=subprocess.DEVNULL)
        def listening():
            if self.proc.poll() is not None:
                tail = (self.root / "service.log").read_text(errors="replace")[-1500:]
                raise AssertionError(f"the service exited ({self.proc.returncode}) before serving: {tail}")
            return (self.root / "port").exists() and (self.root / "port").read_text() or None

        self.port = int(wait_for(listening, 60, "the service to serve"))
        assert self.request("GET", "/rosart/healthz", headers={})[0] == 200
        return self

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        try:
            conn.request(method, path, body=body, headers=WHO if headers is None else headers)
            reply = conn.getresponse()
            return reply.status, {k.lower(): v for k, v in reply.getheaders()}, reply.read().decode("utf-8")
        finally:
            conn.close()

    def press(self, path, fields=None):
        _, _, home = self.request("GET", "/rosart/")
        token = re.search(r'name="csrf" value="([^"]+)"', home).group(1)
        body = urllib.parse.urlencode({"csrf": token, **(fields or {})})
        status, headers, text = self.request("POST", f"/rosart{path}", body, {
            **WHO, "Origin": ORIGIN, "Content-Type": "application/x-www-form-urlencoded"})
        return status, headers, text

    def job(self, job_id):
        return self.store.get(job_id)

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(10)
        log = getattr(self, "log", None)
        if log is not None:
            log.close()


@pytest.fixture()
def world(tmp_path):
    flow = Flow(tmp_path)
    snapshot_id = flow.checked()
    started = []

    def service(scenario=None, env=None):
        svc = Service(tmp_path, scenario, env)
        started.append(svc)
        return svc

    yield tmp_path, snapshot_id, service
    for svc in started:
        svc.stop()
    for pid_file in list(tmp_path.rglob("*.pid")) + [tmp_path / "aws.pid"]:
        try:
            os.killpg(int(pid_file.read_text()), signal.SIGKILL)
        except (OSError, ValueError):
            pass


def scenario_for(root, block_on_pass=None, **extra):
    data = {"profile": "rosart-deploy", "bucket": "rosart-test-bucket", "region": "ap-southeast-2",
            "distribution_id": "E2ABCDEF123456", "log": str(root / "aws.log"), "started_file": str(root / "aws.pid"),
            "append_file": str(root / "append.txt"), "ignore_sigterm": True, **extra}
    if block_on_pass:
        data["block_on_pass"] = block_on_pass
    return data


def job_id_of(location):
    return int(location.rsplit("/", 1)[1])


def real_syncs(root):
    lines = [json.loads(line) for line in (root / "aws.log").read_text().splitlines()]
    return [c for c in lines if c[1:3] == ["s3", "sync"] and "--dryrun" not in c]


# --- the service is killed mid-publish --------------------------------------------------------------

def test_a_killed_service_leaves_the_aws_child_which_the_next_start_kills_before_it_serves(world):
    root, snapshot_id, make = world
    first = make(scenario_for(root, block_on_pass=3)).start()
    status, headers, _ = first.press("/publish", {"snapshot": snapshot_id})
    assert status == 303
    job_id = job_id_of(headers["location"])
    pid = int(wait_for(lambda: (root / "aws.pid").exists() and (root / "aws.pid").read_text() or None, 60,
                       "the fake aws to block on pass 3"))

    first.proc.kill()
    first.proc.wait(10)

    assert alive(pid), "SIGKILL of the service leaves the aws child running"
    size = (root / "append.txt").stat().st_size
    time.sleep(0.5)
    assert (root / "append.txt").stat().st_size > size, "and it keeps writing"

    second = make(scenario_for(root, block_on_pass=3)).start()

    assert not alive(pid), "the leftover group was killed before the service served"
    size = (root / "append.txt").stat().st_size
    time.sleep(0.5)
    assert (root / "append.txt").stat().st_size == size, "it stopped writing"
    job = second.job(job_id)
    assert job.state == "interrupted" and job.passes_started == 3 and job.child_pgid is None
    page = second.request("GET", f"/rosart/jobs/{job_id}")[2]
    assert "may be partly updated" in page


def test_a_republish_after_the_interruption_runs_all_six_passes_and_invalidates(world):
    root, snapshot_id, make = world
    first = make(scenario_for(root, block_on_pass=3)).start()
    status, headers, _ = first.press("/publish", {"snapshot": snapshot_id})
    wait_for(lambda: (root / "aws.pid").exists(), 60, "pass 3 to block")
    first.proc.kill()
    first.proc.wait(10)
    before = len(real_syncs(root))
    second = make(scenario_for(root)).start()

    status, headers, _ = second.press("/publish", {"snapshot": snapshot_id})
    assert status == 303
    again = job_id_of(headers["location"])
    wait_for(lambda: second.job(again).state != "running", 60, "the re-publish to finish")

    assert second.job(again).state in ("published", "published-cache-pending")
    runs = real_syncs(root)[before:]
    assert len(runs) == 6 and all("--delete" in c for c in runs[1:]) and "--delete" not in runs[0]
    log = [json.loads(line) for line in (root / "aws.log").read_text().splitlines()]
    assert any(c[1:3] == ["cloudfront", "create-invalidation"] for c in log)


def test_a_reused_pid_is_not_killed_at_start_up(world):
    root, snapshot_id, make = world
    sleeper = subprocess.Popen(["sleep", "300"], start_new_session=True)
    try:
        svc = make(scenario_for(root))
        job = svc.store.start_job("check", "maria", "Maria Lopez", "m@example.org", "abc1234", 1200)
        svc.store.set_child(job.id, os.getpgid(sleeper.pid), 1)          # a start time that cannot match

        svc.start()

        assert alive(sleeper.pid), "a process whose start time differs is a reused PID and is left alone"
        assert svc.job(job.id).state == "interrupted"
    finally:
        sleeper.kill()
        sleeper.wait(10)


# --- SIGTERM ----------------------------------------------------------------------------------------

def test_sigterm_with_a_child_running_kills_the_group_writes_interrupted_and_exits_in_time(world):
    root, snapshot_id, make = world
    svc = make(scenario_for(root, block_on_pass=3)).start()
    status, headers, _ = svc.press("/publish", {"snapshot": snapshot_id})
    job_id = job_id_of(headers["location"])
    pid = int(wait_for(lambda: (root / "aws.pid").exists() and (root / "aws.pid").read_text() or None, 60,
                       "pass 3 to block"))

    started = time.monotonic()
    svc.proc.send_signal(signal.SIGTERM)
    svc.proc.wait(30)

    assert time.monotonic() - started < 30
    assert not alive(pid) and svc.job(job_id).state == "interrupted"
    assert svc.job(job_id).child_pgid is None


# --- a worker thread that dies ----------------------------------------------------------------------

def test_a_dead_worker_during_a_build_is_cleaned_up_by_the_watchdog_and_the_service_keeps_serving(world):
    root, snapshot_id, make = world
    svc = make(scenario_for(root), env={"PUBLISHER_FAULT_HOOK": "build"}).start()

    status, headers, _ = svc.press("/check")
    assert status == 303
    job_id = job_id_of(headers["location"])
    pid_file = wait_for(lambda: next(iter(root.rglob("npm-build.pid")), None), 60, "the fake build to start")
    pid = int(pid_file.read_text())

    started = time.monotonic()
    wait_for(lambda: svc.job(job_id).state != "running", 12, "the watchdog to clean up")

    job = svc.job(job_id)
    assert time.monotonic() - started < 12
    assert job.state == "failed" and job.reason_code == "worker-died" and job.child_pgid is None
    wait_for(lambda: not alive(pid), 10, "the build child to be gone")
    assert svc.request("GET", "/rosart/healthz")[0] == 200
    status, _, _ = svc.press("/check")
    assert status == 303, "the lock is free: a new check starts"


def test_a_dead_worker_during_pass_two_ends_publish_failed_partial(world):
    root, snapshot_id, make = world
    svc = make(scenario_for(root, block_on_pass=2), env={"PUBLISHER_FAULT_HOOK": "pass-2"}).start()

    status, headers, _ = svc.press("/publish", {"snapshot": snapshot_id})
    job_id = job_id_of(headers["location"])
    wait_for(lambda: svc.job(job_id).state != "running", 12, "the watchdog to clean up")

    job = svc.job(job_id)
    assert job.state == "publish-failed-partial" and job.reason_code == "worker-died" and job.passes_started == 2
    pid_file = root / "aws.pid"
    if pid_file.exists():
        wait_for(lambda: not alive(int(pid_file.read_text())), 10, "the aws child to be gone")
    assert svc.request("GET", "/rosart/healthz")[0] == 200
