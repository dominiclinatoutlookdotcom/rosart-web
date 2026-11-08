"""Tests for the fake AWS used by the publisher tests (spec 006 T025).

Two seams: ``FakeAws``, the runner injected into ``publish.Aws``, and ``bin/aws``, a fake executable
for process-level tests. Neither touches the network.
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from fake_aws import FakeAws, MARKER_KEY
from publish import Aws, DeployConfig, PublishError

CONFIG = DeployConfig("rosart-deploy", "rosart-site", "ap-southeast-2", "E2ABCDEF123456")
BIN = Path(__file__).parent / "bin" / "aws"
MARKER = {"site": "rosart", "publish_id": "box-7", "published_at": "2026-11-08T01:02:03Z",
          "content_hash": "a" * 64, "publisher": "box"}


def make(temp_dir=None, **kwargs) -> tuple[FakeAws, Aws]:
    fake = FakeAws(CONFIG, **kwargs)
    return fake, Aws(CONFIG, fake, temp_dir=temp_dir)


def test_identity_answers_the_deploy_user_by_default_and_can_be_overridden():
    _, aws = make()
    assert aws.json(["sts", "get-caller-identity"])["Arn"].endswith(":user/rosart-web-deploy")
    _, other = make(identity_arn="arn:aws:iam::123456789012:user/someone-else")
    assert other.json(["sts", "get-caller-identity"])["Arn"].endswith(":user/someone-else")


def test_dry_run_sync_output_is_prefixed_and_the_real_one_is_not():
    fake, aws = make()
    dry = aws.run(["s3", "sync", "dist", "s3://rosart-site", "--delete", "--dryrun"]).stdout
    real = aws.run(["s3", "sync", "dist", "s3://rosart-site", "--delete"]).stdout
    assert dry.startswith("(dryrun) upload:") and "(dryrun) delete:" in dry
    assert real.startswith("upload:") and "dryrun" not in real
    assert [c[1:3] for c in fake.commands] == [["s3", "sync"], ["s3", "sync"]]


def test_get_object_writes_the_marker_to_the_local_file(tmp_path):
    _, aws = make(tmp_path, marker=MARKER)
    target = tmp_path / "marker.json"
    aws.run(["s3api", "get-object", "--bucket", "rosart-site", "--key", MARKER_KEY, str(target)])
    assert json.loads(target.read_text(encoding="utf-8")) == MARKER


def test_get_object_with_no_marker_fails_with_nosuchkey(tmp_path):
    _, aws = make(tmp_path)
    with pytest.raises(PublishError, match="NoSuchKey"):
        aws.run(["s3api", "get-object", "--bucket", "rosart-site", "--key", MARKER_KEY,
                 str(tmp_path / "m.json")])


def test_get_object_can_fail_with_another_error(tmp_path):
    _, aws = make(tmp_path, get_error="AccessDenied")
    with pytest.raises(PublishError, match="AccessDenied"):
        aws.run(["s3api", "get-object", "--bucket", "rosart-site", "--key", MARKER_KEY,
                 str(tmp_path / "m.json")])


def test_put_object_stores_the_body_and_a_later_get_returns_it(tmp_path):
    fake, aws = make(tmp_path)
    body = tmp_path / "body.json"
    body.write_text(json.dumps(MARKER), encoding="utf-8")
    aws.run(["s3api", "put-object", "--bucket", "rosart-site", "--key", MARKER_KEY, "--body", str(body),
             "--content-type", "application/json", "--cache-control", "no-store"])
    assert fake.marker == MARKER and len(fake.marker_writes) == 1
    target = tmp_path / "back.json"
    aws.run(["s3api", "get-object", "--bucket", "rosart-site", "--key", MARKER_KEY, str(target)])
    assert json.loads(target.read_text(encoding="utf-8")) == MARKER


def test_invalidation_answers_an_id_and_the_waiter_can_exit_255():
    _, aws = make(wait_exit=255)
    created = aws.json(["cloudfront", "create-invalidation", "--distribution-id", "E2ABCDEF123456",
                        "--paths", "/*"])
    assert created["Invalidation"]["Id"] == "I2EXAMPLE"
    with pytest.raises(PublishError, match="exit 255"):
        aws.run(["cloudfront", "wait", "invalidation-completed", "--distribution-id", "E2ABCDEF123456",
                 "--id", "I2EXAMPLE"])


def test_failure_injection_hits_only_the_chosen_real_pass_and_never_a_dry_run():
    _, aws = make(fail_passes={2})
    sync = ["s3", "sync", "dist", "s3://rosart-site"]
    aws.run([*sync, "--dryrun"])
    aws.run([*sync, "--dryrun"])
    aws.run(sync)                                   # pass 1
    with pytest.raises(PublishError, match="AccessDenied"):
        aws.run(sync)                               # pass 2
    aws.run(sync)                                   # pass 3


def test_the_runner_re_checks_the_guard_on_every_command_it_receives():
    fake = FakeAws(CONFIG)
    with pytest.raises(PublishError, match="not allowed"):
        fake(["aws", "s3", "rm", "s3://rosart-site", "--recursive", "--profile", "rosart-deploy"])
    assert fake.commands == []


# --- the fake executable ------------------------------------------------------------------

def scenario_file(tmp_path, **scenario) -> dict:
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps({"profile": "rosart-deploy", "bucket": "rosart-site",
                                "region": "ap-southeast-2", "distribution_id": "E2ABCDEF123456",
                                **scenario}), encoding="utf-8")
    return {**os.environ, "FAKE_AWS_SCENARIO": str(path)}


def run_aws(env, *args, **kwargs):
    return subprocess.run([sys.executable, str(BIN), *args], env=env, capture_output=True, text=True,
                          **kwargs)


def test_executable_answers_identity_and_logs_every_invocation(tmp_path):
    log = tmp_path / "calls.jsonl"
    env = scenario_file(tmp_path, log=str(log))
    done = run_aws(env, "sts", "get-caller-identity", "--profile", "rosart-deploy", "--region",
                   "ap-southeast-2", "--output", "json")
    assert done.returncode == 0
    assert json.loads(done.stdout)["Arn"].endswith(":user/rosart-web-deploy")
    assert json.loads(log.read_text(encoding="utf-8").splitlines()[0])[:3] == ["aws", "sts", "get-caller-identity"]


def test_executable_keeps_the_marker_between_invocations(tmp_path):
    env = scenario_file(tmp_path)
    body = tmp_path / "body.json"
    body.write_text(json.dumps(MARKER), encoding="utf-8")
    common = ["--bucket", "rosart-site", "--key", MARKER_KEY, "--profile", "rosart-deploy",
              "--region", "ap-southeast-2", "--output", "json"]
    missing = run_aws(env, "s3api", "get-object", str(tmp_path / "a.json"), *common)
    assert missing.returncode != 0 and "NoSuchKey" in missing.stderr
    assert run_aws(env, "s3api", "put-object", "--body", str(body), "--content-type", "application/json",
                   "--cache-control", "no-store", *common).returncode == 0
    assert run_aws(env, "s3api", "get-object", str(tmp_path / "b.json"), *common).returncode == 0
    assert json.loads((tmp_path / "b.json").read_text(encoding="utf-8")) == MARKER


def sync_args(*extra):
    return ["s3", "sync", "dist", "s3://rosart-site", *extra, "--profile", "rosart-deploy",
            "--region", "ap-southeast-2"]


def wait_for(path: Path) -> None:
    deadline = time.monotonic() + 10
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.05)


def test_executable_blocks_on_the_chosen_pass_and_appends_to_a_file(tmp_path):
    started, ticks = tmp_path / "started", tmp_path / "ticks.txt"
    env = scenario_file(tmp_path, block_on_pass=2, started_file=str(started), append_file=str(ticks))
    assert run_aws(env, *sync_args()).returncode == 0           # pass 1 returns
    child = subprocess.Popen([sys.executable, str(BIN), *sync_args()], env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for(started)
        assert started.exists() and child.poll() is None
        time.sleep(0.5)
        assert len(ticks.read_text(encoding="utf-8").splitlines()) >= 2
    finally:
        child.kill()
        child.wait()


@pytest.mark.skipif(os.name == "nt", reason="POSIX signals")
def test_executable_can_ignore_sigterm(tmp_path):
    started = tmp_path / "started"
    env = scenario_file(tmp_path, block_on_pass=1, ignore_sigterm=True, started_file=str(started))
    child = subprocess.Popen([sys.executable, str(BIN), *sync_args()], env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_for(started)
        child.send_signal(signal.SIGTERM)
        time.sleep(0.5)
        assert child.poll() is None
    finally:
        child.kill()
        child.wait()
