"""The bucket marker `_publish.json` (spec 006 T029; E1, R-3 box half, R-7 PC half).

Seams: ``dist_hash``, ``read_marker``, ``write_marker`` against the fake runner, and the CLI's
``main`` for the PC path. No AWS is called.
"""
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

import publish as publish_module
from publish import Aws, AwsResult, DeployConfig, PublishError, main

from .fake_aws import FakeAws

CONFIG = DeployConfig("rosart-deploy", "rosart-site", "ap-southeast-2", "E2ABCDEF123456")
MARKER = {"site": "rosart", "publish_id": "box-7", "published_at": "2026-11-08T01:02:03Z",
          "content_hash": "b" * 64, "publisher": "box"}
NOW = datetime(2026, 11, 8, 1, 2, 3, tzinfo=timezone.utc)


def make_dist(root: Path, pages: int = 12) -> Path:
    dist = root / "dist"
    (dist / "_astro").mkdir(parents=True)
    (dist / "_astro" / "site.Ab12Cd.css").write_text("body{}", encoding="utf-8")
    (dist / "index.html").write_text("<h1>Rosart</h1>", encoding="utf-8")
    (dist / "404.html").write_text("<h1>Not found</h1>", encoding="utf-8")
    (dist / "robots.txt").write_text("User-agent: *", encoding="utf-8")
    (dist / "sitemap-index.xml").write_text("<sitemapindex/>", encoding="utf-8")
    for n in range(pages):
        page = dist / "products" / f"p{n}" / "index.html"
        page.parent.mkdir(parents=True)
        page.write_text(f"<h1>{n}</h1>", encoding="utf-8")
    return dist


def write_config(tmp_path: Path) -> Path:
    path = tmp_path / "deploy.config.json"
    path.write_text(json.dumps({"profile": "rosart-deploy", "bucket": "rosart-site",
                                "region": "ap-southeast-2", "distributionId": "E2ABCDEF123456"}),
                    encoding="utf-8")
    return path


def no_prompt(_question):
    raise AssertionError("must not prompt")


# --- dist_hash ----------------------------------------------------------------------------

def test_dist_hash_is_the_sha256_of_the_sorted_digest_and_path_lines(tmp_path):
    dist = tmp_path / "dist"
    (dist / "b").mkdir(parents=True)
    (dist / "a.txt").write_bytes(b"alpha")
    (dist / "b" / "c.txt").write_bytes(b"gamma")
    lines = "".join(f"{hashlib.sha256(data).hexdigest()}  {rel}\n"
                    for rel, data in [("a.txt", b"alpha"), ("b/c.txt", b"gamma")])
    assert publish_module.dist_hash(dist) == hashlib.sha256(lines.encode("utf-8")).hexdigest()
    assert re.fullmatch(r"[0-9a-f]{64}", publish_module.dist_hash(dist))


def test_dist_hash_changes_with_content_and_with_a_rename_but_not_with_creation_order(tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    for root, order in ((first, ["a", "b"]), (second, ["b", "a"])):
        root.mkdir()
        for name in order:
            (root / name).write_text(name, encoding="utf-8")
    assert publish_module.dist_hash(first) == publish_module.dist_hash(second)
    (second / "a").write_text("changed", encoding="utf-8")
    assert publish_module.dist_hash(first) != publish_module.dist_hash(second)
    (second / "a").write_text("a", encoding="utf-8")
    (second / "a").rename(second / "z")
    assert publish_module.dist_hash(first) != publish_module.dist_hash(second)


def test_dist_hash_ignores_a_stray_marker_file_that_no_sync_publishes(tmp_path):
    dist = make_dist(tmp_path)
    before = publish_module.dist_hash(dist)
    (dist / "_publish.json").write_text("{}", encoding="utf-8")
    assert publish_module.dist_hash(dist) == before


# --- read_marker and write_marker ---------------------------------------------------------

def test_read_marker_returns_the_stored_object_with_the_exact_get_object_command():
    fake = FakeAws(CONFIG, marker=MARKER)
    assert publish_module.read_marker(Aws(CONFIG, fake)) == MARKER
    [command] = fake.commands
    assert command[:7] == ["aws", "s3api", "get-object", "--bucket", "rosart-site", "--key", "_publish.json"]
    assert command[8:] == ["--profile", "rosart-deploy", "--region", "ap-southeast-2", "--no-cli-pager",
                           "--output", "json"]


def test_read_marker_is_none_when_the_key_is_absent():
    assert publish_module.read_marker(Aws(CONFIG, FakeAws(CONFIG))) is None


def test_read_marker_raises_on_any_other_error():
    with pytest.raises(PublishError, match="AccessDenied"):
        publish_module.read_marker(Aws(CONFIG, FakeAws(CONFIG, get_error="AccessDenied")))


def test_read_marker_raises_on_a_body_that_is_not_a_json_object():
    class Garbage(FakeAws):
        def _get_object(self, command):
            Path(self._local_file(command)).write_text("[1]", encoding="utf-8")
            return AwsResult(0, "{}", "")

    with pytest.raises(PublishError, match="marker"):
        publish_module.read_marker(Aws(CONFIG, Garbage(CONFIG)))


def test_write_marker_runs_exactly_the_put_object_command_with_the_marker_as_body():
    fake = FakeAws(CONFIG)
    publish_module.write_marker(Aws(CONFIG, fake), MARKER)
    [command] = fake.commands
    body = command[command.index("--body") + 1]
    assert command == ["aws", "s3api", "put-object", "--bucket", "rosart-site", "--key", "_publish.json",
                       "--body", body, "--content-type", "application/json", "--cache-control", "no-store",
                       "--profile", "rosart-deploy", "--region", "ap-southeast-2", "--no-cli-pager",
                       "--output", "json"]
    assert fake.marker == MARKER


def test_write_marker_failure_raises_and_names_the_marker():
    with pytest.raises(PublishError, match="_publish.json"):
        publish_module.write_marker(Aws(CONFIG, FakeAws(CONFIG, fail=lambda c: c[1:3] == ["s3api", "put-object"])), MARKER)


# --- the CLI path (R-7): same marker, publisher pc ------------------------------------------

@pytest.fixture
def site(tmp_path):
    return ["--config", str(write_config(tmp_path)), "--dist", str(make_dist(tmp_path))]


def run_cli(site, fake, *flags):
    return main([*site, "--yes", *flags], runner=fake, ask=no_prompt, clock=lambda: NOW)


def test_the_cli_writes_the_pc_marker_once_after_pass_6_and_before_the_invalidation(site, tmp_path):
    fake = FakeAws(CONFIG)
    assert run_cli(site, fake, "--wait") == 0
    kinds = [tuple(c[1:3]) for c in fake.commands]
    real_syncs = [i for i, c in enumerate(fake.commands) if c[1:3] == ["s3", "sync"] and "--dryrun" not in c]
    [put] = [i for i, k in enumerate(kinds) if k == ("s3api", "put-object")]
    [invalidation] = [i for i, k in enumerate(kinds) if k == ("cloudfront", "create-invalidation")]
    assert len(real_syncs) == 6 and put == real_syncs[-1] + 1 and invalidation == put + 1
    assert len(fake.marker_writes) == 1


def test_the_pc_marker_has_exactly_the_documented_fields_and_no_person(site, tmp_path):
    fake = FakeAws(CONFIG)
    run_cli(site, fake)
    [marker] = fake.marker_writes
    assert marker == {"site": "rosart", "publisher": "pc", "publish_id": "pc-20261108T010203Z",
                      "published_at": "2026-11-08T01:02:03Z",
                      "content_hash": publish_module.dist_hash(tmp_path / "dist")}


def test_the_cli_reads_the_marker_after_the_identity_and_before_the_dry_run(site):
    fake = FakeAws(CONFIG)
    run_cli(site, fake)
    kinds = [tuple(c[1:3]) for c in fake.commands]
    assert kinds[0] == ("sts", "get-caller-identity") and kinds[1] == ("s3api", "get-object")
    assert kinds[2] == ("s3", "sync") and "--dryrun" in fake.commands[2]


def test_a_marker_read_failure_other_than_nosuchkey_stops_before_any_sync(site, capsys):
    fake = FakeAws(CONFIG, get_error="AccessDenied")
    assert run_cli(site, fake) == 1
    assert not fake.ran("s3") and not fake.ran("cloudfront")
    assert "AccessDenied" in capsys.readouterr().err


def test_a_failed_marker_write_stops_before_the_invalidation_and_says_so(site, capsys):
    fake = FakeAws(CONFIG, fail=lambda c: c[1:3] == ["s3api", "put-object"])
    assert run_cli(site, fake) == 1
    assert not fake.ran("cloudfront")
    assert "_publish.json" in capsys.readouterr().err


def test_a_dry_run_or_a_failed_pass_never_writes_the_marker(site):
    dry = FakeAws(CONFIG)
    assert main([*site, "--dry-run"], runner=dry, ask=no_prompt, clock=lambda: NOW) == 0
    assert not dry.ran("s3api", "put-object")
    failing = FakeAws(CONFIG, fail_passes={6})
    assert run_cli(site, failing) == 1
    assert not failing.ran("s3api", "put-object")


def test_a_second_publish_replaces_the_marker_it_read(site):
    fake = FakeAws(CONFIG, marker=MARKER)
    assert run_cli(site, fake) == 0
    assert fake.marker["publisher"] == "pc" and fake.marker != MARKER
