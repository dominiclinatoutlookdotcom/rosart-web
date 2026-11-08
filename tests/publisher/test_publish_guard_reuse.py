"""The service's AWS runner reuses the publish guard (spec 006 T033; R-3, research R-8).

Seams: ``publisher.aws.service_aws`` (a ``publish.Aws`` around the service runner) and
``ServiceAwsRunner`` with an injected process runner. Nothing starts a real ``aws``.
"""
import re
from pathlib import Path

import pytest

import publish as publish_module
from publish import Aws, AwsResult, DeployConfig, PublishError, assert_command_allowed

from fake_aws import FakeAws

CONFIG = DeployConfig("rosart-deploy", "rosart-site", "ap-southeast-2", "E2ABCDEF123456")
SERVICE_DIR = Path(__file__).resolve().parent.parent.parent / "publisher"


def make_dist(root: Path) -> Path:
    dist = root / "dist"
    (dist / "_astro").mkdir(parents=True)
    (dist / "_astro" / "site.css").write_text("body{}", encoding="utf-8")
    for name in ("index.html", "404.html", "robots.txt", "sitemap-index.xml"):
        (dist / name).write_text("x", encoding="utf-8")
    return dist


def test_every_command_of_a_full_publish_through_the_service_runner_passes_the_guard(tmp_path):
    from publisher.aws import service_aws

    fake = FakeAws(CONFIG)
    work = tmp_path / "job"
    work.mkdir()
    aws = service_aws(CONFIG, fake, temp_dir=work)
    dist = make_dist(tmp_path)
    passes = publish_module.sync_passes(CONFIG, dist)

    publish_module.show_identity(aws)
    publish_module.read_marker(aws)
    publish_module.plan(aws, passes)
    publish_module.sync(aws, passes)
    publish_module.write_marker(aws, {"site": "rosart"})
    publish_module.invalidate(aws, wait=True)

    assert len(fake.commands) > 10
    for command in fake.commands:
        assert_command_allowed(command, CONFIG, work)


def test_service_aws_is_a_publish_aws_for_the_configured_target(tmp_path):
    from publisher.aws import service_aws

    aws = service_aws(CONFIG, FakeAws(CONFIG), temp_dir=tmp_path)
    assert isinstance(aws, Aws) and aws.config == CONFIG and aws.temp_dir == tmp_path


@pytest.mark.parametrize("command", [
    ["s3", "sync", "dist", "s3://other-bucket"],
    ["s3", "rm", "s3://rosart-site", "--recursive"],
    ["s3", "sync", "dist", "s3://rosart-site", "--profile", "admin"],
    ["cloudfront", "create-invalidation", "--distribution-id", "EOTHER123456", "--paths", "/*"],
    ["s3api", "delete-object", "--bucket", "rosart-site", "--key", "_publish.json"],
])
def test_a_wrong_bucket_profile_distribution_or_operation_raises_before_the_runner_is_called(command, tmp_path):
    from publisher.aws import service_aws

    calls = []
    aws = service_aws(CONFIG, lambda cmd: calls.append(cmd) or AwsResult(0, "", ""), temp_dir=tmp_path)
    with pytest.raises(PublishError):
        aws.run(command)
    assert calls == []


def test_a_config_with_another_profile_puts_that_profile_on_every_command(tmp_path):
    from publisher.aws import service_aws

    other = DeployConfig("other-profile", "rosart-site", "ap-southeast-2", "E2ABCDEF123456")
    fake = FakeAws(other)
    service_aws(other, fake, temp_dir=tmp_path).run(["sts", "get-caller-identity"])
    assert fake.commands[0].count("--profile") == 1 and "other-profile" in fake.commands[0]


# --- the process runner: one credential source --------------------------------------------

def test_the_aws_child_gets_the_credentials_file_under_the_aws_folder_and_no_key_variables(tmp_path, monkeypatch):
    from publisher.aws import ServiceAwsRunner

    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE",
                 "AWS_DEFAULT_PROFILE"):
        monkeypatch.setenv(name, "from-env")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", "/home/someone/.aws/credentials")
    monkeypatch.setenv("AWS_PAGER", "less")
    aws_dir = tmp_path / "aws"
    from publisher.runner import Completed

    class RecordingChildRunner:
        def __init__(self):
            self.calls = []

        def mono(self):
            return 0.0

        def run(self, argv, env, cwd, timeout_mono):
            self.calls.append((argv, env))
            return Completed(0, "{}", "")

    children = RecordingChildRunner()
    runner = ServiceAwsRunner(aws_dir, children, executable="/usr/bin/aws")
    result = runner(["aws", "sts", "get-caller-identity", "--profile", "rosart-deploy"])

    [(command, env)] = children.calls
    assert command == ["/usr/bin/aws", "sts", "get-caller-identity", "--profile", "rosart-deploy"]
    assert env["AWS_SHARED_CREDENTIALS_FILE"] == str(aws_dir / "credentials")
    assert env["AWS_CONFIG_FILE"] == str(aws_dir / "config")
    assert env["AWS_EC2_METADATA_DISABLED"] == "true" and env["AWS_PAGER"] == ""
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE",
                 "AWS_DEFAULT_PROFILE"):
        assert name not in env
    assert result == AwsResult(0, "{}", "")


def test_the_process_runner_refuses_a_missing_aws_cli(tmp_path, monkeypatch):
    from publisher.aws import ServiceAwsRunner

    monkeypatch.setattr("shutil.which", lambda name: None)
    with pytest.raises(PublishError, match="not on PATH"):
        ServiceAwsRunner(tmp_path / "aws", object())


def test_no_other_code_in_the_service_package_starts_aws():
    offenders = []
    for path in sorted(SERVICE_DIR.rglob("*.py")):
        if path.name == "aws.py" and path.parent == SERVICE_DIR:
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"""\[\s*["']aws["']|which\(\s*["']aws|\baws\s+(s3|sts|cloudfront)\b""", text):
            offenders.append(path.name)
    assert offenders == []
