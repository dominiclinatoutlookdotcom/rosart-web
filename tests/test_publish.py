"""Tests for the publish script with a fake `aws` runner: nothing here touches AWS."""
import fnmatch
import json
import mimetypes
import os
import re
import time
import shutil
import subprocess
from pathlib import Path

import pytest

from publish import (
    AwsCliRunner,
    AwsResult,
    DeployConfig,
    PublishError,
    assert_command_allowed,
    check_dist,
    load_config,
    main,
)

FILLED = {"profile": "rosart-deploy", "bucket": "rosart-site", "region": "ap-southeast-2",
          "distributionId": "E2ABCDEF123456"}


def write_config(tmp_path: Path, **overrides) -> Path:
    path = tmp_path / "deploy.config.json"
    path.write_text(json.dumps({**FILLED, **overrides}), encoding="utf-8")
    return path


# --- config -------------------------------------------------------------------------------

def test_filled_config_loads(tmp_path):
    config = load_config(write_config(tmp_path))
    assert (config.profile, config.bucket, config.region, config.distribution_id) == (
        "rosart-deploy", "rosart-site", "ap-southeast-2", "E2ABCDEF123456")


def test_placeholder_config_is_refused_naming_every_unfilled_field(tmp_path):
    path = write_config(tmp_path, bucket="REPLACE_ME", region="REPLACE_ME", distributionId="")
    with pytest.raises(PublishError) as error:
        load_config(path)
    message = str(error.value)
    assert "not filled in: bucket, region, distributionId" in message


def test_the_committed_config_is_filled_and_valid():
    # Filled in 6c024f1 (2026-11-07) once the bucket and distribution existed.
    committed = Path(__file__).resolve().parent.parent / "deploy.config.json"
    config = load_config(committed)
    assert config.profile == "rosart-deploy"


def test_profile_defaults_to_rosart_deploy(tmp_path):
    path = tmp_path / "deploy.config.json"
    path.write_text(json.dumps({k: v for k, v in FILLED.items() if k != "profile"}), encoding="utf-8")
    assert load_config(path).profile == "rosart-deploy"


@pytest.mark.parametrize("field, value", [
    ("bucket", "Rosart_Site"),            # not a valid S3 bucket name
    ("bucket", "s3://rosart-site"),       # a URL, not a name
    ("region", "sydney"),
    ("distributionId", "e2abc def"),
    ("profile", "rosart deploy"),
])
def test_malformed_values_are_refused(tmp_path, field, value):
    with pytest.raises(PublishError, match=field):
        load_config(write_config(tmp_path, **{field: value}))


def test_unknown_keys_such_as_credentials_are_refused(tmp_path):
    with pytest.raises(PublishError, match="aws_secret_access_key"):
        load_config(write_config(tmp_path, aws_secret_access_key="x"))


def test_missing_config_file_is_refused(tmp_path):
    with pytest.raises(PublishError, match="no deploy config"):
        load_config(tmp_path / "nope.json")


# --- dist preflight -----------------------------------------------------------------------

def make_dist(root: Path, pages: int = 12) -> Path:
    """A minimal built site: home, 404, robots, sitemap, hashed assets and ``pages`` more pages."""
    dist = root / "dist"
    (dist / "_astro").mkdir(parents=True)
    (dist / "_astro" / "site.Ab12Cd.css").write_text("body{}", encoding="utf-8")
    (dist / "_astro" / "rose.Xy34.webp").write_bytes(b"RIFF")
    (dist / "index.html").write_text("<h1>Rosart</h1>", encoding="utf-8")
    (dist / "404.html").write_text("<h1>Not found</h1>", encoding="utf-8")
    (dist / "robots.txt").write_text("User-agent: *", encoding="utf-8")
    (dist / "sitemap-index.xml").write_text("<sitemapindex/>", encoding="utf-8")
    for n in range(pages):
        page = dist / "products" / f"p{n}" / "index.html"
        page.parent.mkdir(parents=True)
        page.write_text(f"<h1>{n}</h1>", encoding="utf-8")
    return dist


def test_complete_dist_passes_and_counts_files(tmp_path):
    summary = check_dist(make_dist(tmp_path))
    assert summary.pages == 13 and summary.files == 18


def test_missing_dist_is_refused(tmp_path):
    with pytest.raises(PublishError, match="does not exist"):
        check_dist(tmp_path / "dist")


def test_empty_dist_is_refused(tmp_path):
    (tmp_path / "dist").mkdir()
    with pytest.raises(PublishError, match="index.html"):
        check_dist(tmp_path / "dist")


@pytest.mark.parametrize("missing", ["index.html", "404.html", "robots.txt", "sitemap-index.xml"])
def test_dist_without_a_required_file_is_refused(tmp_path, missing):
    dist = make_dist(tmp_path)
    (dist / missing).unlink()
    with pytest.raises(PublishError, match=missing):
        check_dist(dist)


def test_dist_with_an_empty_home_page_is_refused(tmp_path):
    dist = make_dist(tmp_path)
    (dist / "index.html").write_text("", encoding="utf-8")
    with pytest.raises(PublishError, match="index.html"):
        check_dist(dist)


def test_dist_with_too_few_pages_is_refused(tmp_path):
    with pytest.raises(PublishError, match="pages"):
        check_dist(make_dist(tmp_path, pages=2))


def test_dist_without_built_assets_is_refused(tmp_path):
    dist = make_dist(tmp_path)
    for asset in (dist / "_astro").iterdir():
        asset.unlink()
    with pytest.raises(PublishError, match="_astro"):
        check_dist(dist)


# --- command guard ------------------------------------------------------------------------

CONFIG = DeployConfig("rosart-deploy", "rosart-site", "ap-southeast-2", "E2ABCDEF123456")
P = ["--profile", "rosart-deploy"]


@pytest.mark.parametrize("command", [
    ["aws", "sts", "get-caller-identity", *P],
    ["aws", "s3", "sync", "dist", "s3://rosart-site", "--delete", *P],
    ["aws", "s3", "sync", "dist/_astro", "s3://rosart-site/_astro", *P],
    ["aws", "cloudfront", "create-invalidation", "--distribution-id", "E2ABCDEF123456",
     "--paths", "/*", *P],
    ["aws", "cloudfront", "wait", "invalidation-completed", "--distribution-id", "E2ABCDEF123456",
     "--id", "I1", *P],
])
def test_guard_allows_the_publish_commands(command):
    assert_command_allowed(command, CONFIG)


@pytest.mark.parametrize("command, reason", [
    (["aws", "sts", "get-caller-identity"], "--profile"),
    (["aws", "sts", "get-caller-identity", "--profile", "default"], "--profile"),
    (["aws", "sts", "get-caller-identity", *P, "--profile", "admin"], "--profile"),
    (["aws", "sts", "get-caller-identity", *P, "--profile=admin"], "--profile"),
    (["aws", "s3", "sync", "dist", "s3://other-bucket", *P], "other-bucket"),
    (["aws", "s3", "sync", "dist", "s3://rosart-site-backup/x", *P], "rosart-site-backup"),
    (["aws", "s3", "sync", "s3://rosart-site", "dist", *P], "upload"),
    (["aws", "s3", "rm", "s3://rosart-site", "--recursive", *P], "not allowed"),
    (["aws", "s3api", "delete-bucket", "--bucket", "rosart-site", *P], "not allowed"),
    (["aws", "cloudfront", "create-invalidation", "--distribution-id", "EOTHER123456",
      "--paths", "/*", *P], "EOTHER123456"),
    (["aws", "cloudfront", "create-invalidation", "--paths", "/*", *P], "--distribution-id"),
])
def test_guard_refuses_other_profiles_buckets_operations_and_distributions(command, reason):
    with pytest.raises(PublishError, match=re.escape(reason)):
        assert_command_allowed(command, CONFIG)


# --- the publish flow, through main() with a fake aws ------------------------------------

IDENTITY = {"UserId": "AIDAEXAMPLE", "Account": "123456789012",
            "Arn": "arn:aws:iam::123456789012:user/rosart-deploy"}
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "public, max-age=0, must-revalidate"


class FakeAws:
    """Records every aws command and answers like the CLI would; ``fail`` picks commands to fail."""

    def __init__(self, fail=lambda command: False):
        self.commands: list[list[str]] = []
        self.fail = fail

    def __call__(self, command):
        self.commands.append(list(command))
        if self.fail(command):
            return AwsResult(1, "", "An error occurred (AccessDenied) when calling the operation")
        args = command[1:]
        if args[:2] == ["sts", "get-caller-identity"]:
            return AwsResult(0, json.dumps(IDENTITY), "")
        if args[:2] == ["cloudfront", "create-invalidation"]:
            return AwsResult(0, json.dumps({"Invalidation": {"Id": "I2EXAMPLE", "Status": "InProgress"}}), "")
        if args[:2] == ["s3", "sync"]:
            return AwsResult(0, self.sync_output(command), "")
        return AwsResult(0, "", "")

    @staticmethod
    def sync_output(command):
        """What `aws s3 sync` prints on Windows: backslash sources, CRLF, `(dryrun) ` prefix."""
        prefix = "(dryrun) " if "--dryrun" in command else ""
        source, dest = command[3], command[4]
        if dest.endswith("/_astro"):
            lines = [f"upload: {source}\\new.Ab12.css to {dest}/new.Ab12.css"]
            if "--delete" in command:
                lines.append(f"delete: {dest}/old.Zz99.css")
        else:
            lines = [f"upload: {source}\\index.html to {dest}/index.html"]
            if "--content-type" not in command:
                lines.append(f"upload: {source}\\robots.txt to {dest}/robots.txt")
            if "--delete" in command:
                lines.append(f"delete: {dest}/old-page/index.html")
        return "\r\n".join(prefix + line for line in lines) + "\r\n"

    def syncs(self, dryrun):
        return [c for c in self.commands if c[1:3] == ["s3", "sync"] and ("--dryrun" in c) == dryrun]

    def ran(self, *words):
        return [c for c in self.commands if c[1:1 + len(words)] == list(words)]


@pytest.fixture
def site(tmp_path):
    """A filled config and a complete dist; returns the argv prefix for main()."""
    return ["--config", str(write_config(tmp_path)), "--dist", str(make_dist(tmp_path))]


def no_prompt(_question):
    raise AssertionError("must not prompt")


def option(command, name):
    return command[command.index(name) + 1] if name in command else None


def filters(command):
    """The --exclude/--include flags of a sync, in order."""
    return [(flag, command[i + 1]) for i, flag in enumerate(command) if flag in ("--exclude", "--include")]


def test_placeholder_config_refuses_before_any_aws_call(tmp_path, capsys):
    aws = FakeAws()
    path = write_config(tmp_path, bucket="REPLACE_ME")
    assert main(["--config", str(path), "--dist", str(make_dist(tmp_path)), "--yes"],
                runner=aws, ask=no_prompt) == 1
    assert aws.commands == []
    assert "not filled in: bucket" in capsys.readouterr().err


def test_check_config_mode_only_validates_the_config(site, capsys):
    aws = FakeAws()
    assert main([*site, "--check-config"], runner=aws, ask=no_prompt) == 0
    assert aws.commands == []
    assert "rosart-site" in capsys.readouterr().out


@pytest.mark.parametrize("break_dist", [
    lambda dist: [p.unlink() for p in dist.rglob("*") if p.is_file()],
    lambda dist: (dist / "404.html").unlink(),
])
def test_incomplete_dist_refuses_before_any_aws_call(site, tmp_path, break_dist, capsys):
    break_dist(tmp_path / "dist")
    aws = FakeAws()
    assert main([*site, "--yes"], runner=aws, ask=no_prompt) == 1
    assert aws.commands == []
    assert "refusing" in capsys.readouterr().err


def test_identity_is_checked_first_and_shown(site, capsys):
    aws = FakeAws()
    assert main([*site, "--yes"], runner=aws, ask=no_prompt) == 0
    assert aws.commands[0][1:3] == ["sts", "get-caller-identity"]
    out = capsys.readouterr().out
    assert "123456789012" in out and "arn:aws:iam::123456789012:user/rosart-deploy" in out


def test_every_aws_command_names_the_profile_and_only_the_configured_bucket(site):
    aws = FakeAws()
    assert main([*site, "--yes", "--wait"], runner=aws, ask=no_prompt) == 0
    assert len(aws.commands) > 5
    for command in aws.commands:
        assert command.count("--profile") == 1 and option(command, "--profile") == "rosart-deploy"
        assert not any(a.startswith("--profile=") for a in command)
        assert option(command, "--region") == "ap-southeast-2"
        for arg in command:
            if arg.startswith("s3://"):
                assert arg == "s3://rosart-site" or arg.startswith("s3://rosart-site/")


def test_asset_syncs_are_confined_to_astro_with_immutable_caching(site, tmp_path):
    aws = FakeAws()
    main([*site, "--yes"], runner=aws, ask=no_prompt)
    assets = [c for c in aws.syncs(dryrun=False) if c[4] == "s3://rosart-site/_astro"]
    assert len(assets) == 2
    for command in assets:
        assert Path(command[3]) == tmp_path / "dist" / "_astro"
        assert option(command, "--cache-control") == IMMUTABLE
        assert filters(command) == []


def test_root_syncs_never_touch_astro_and_revalidate(site, tmp_path):
    aws = FakeAws()
    main([*site, "--yes"], runner=aws, ask=no_prompt)
    root = [c for c in aws.syncs(dryrun=False) if c[4] == "s3://rosart-site"]
    assert len(root) == 4
    for command in root:
        assert Path(command[3]) == tmp_path / "dist"
        assert option(command, "--cache-control") == REVALIDATE
        # Filters apply in order and the last match wins: `_astro/*` excluded last means a root
        # pass neither uploads nor deletes anything under _astro/, whatever came before.
        assert filters(command)[-1] == ("--exclude", "_astro/*")


TYPED = [("*.html", "text/html; charset=utf-8"), ("*.txt", "text/plain; charset=utf-8"),
         ("*.xml", "application/xml; charset=utf-8")]


def test_html_text_and_xml_each_get_a_utf8_typed_pass_that_mirrors_its_own_files(site):
    aws = FakeAws()
    main([*site, "--yes"], runner=aws, ask=no_prompt)
    typed = [c for c in aws.syncs(dryrun=False) if option(c, "--content-type")]
    assert [(filters(c)[1][1], option(c, "--content-type")) for c in typed] == TYPED
    for command, (pattern, _) in zip(typed, TYPED):
        assert filters(command) == [("--exclude", "*"), ("--include", pattern), ("--exclude", "_astro/*")]
        assert option(command, "--cache-control") == REVALIDATE
        # Each typed pass deletes its own stale keys: the general pass excludes these patterns.
        assert "--delete" in command


def test_general_root_pass_leaves_typed_files_to_their_passes(site):
    aws = FakeAws()
    main([*site, "--yes"], runner=aws, ask=no_prompt)
    [general] = [c for c in aws.syncs(dryrun=False)
                 if c[4] == "s3://rosart-site" and not option(c, "--content-type")]
    assert "--delete" in general
    assert filters(general) == [("--exclude", "*.html"), ("--exclude", "*.txt"), ("--exclude", "*.xml"),
                                ("--exclude", "_astro/*")]


def test_real_sync_order_uploads_assets_first_and_prunes_them_last(site):
    aws = FakeAws()
    main([*site, "--yes"], runner=aws, ask=no_prompt)
    shape = [(c[4].removeprefix("s3://rosart-site"), "--delete" in c, bool(option(c, "--content-type")))
             for c in aws.syncs(dryrun=False)]
    # New assets go up before any page that uses them; old assets are deleted only after every
    # page has moved off them; root keys are deleted only by the pass that excludes _astro/.
    assert shape == [("/_astro", False, False), ("", True, True), ("", True, True), ("", True, True),
                     ("", True, False), ("/_astro", True, False)]


def test_dry_run_runs_the_same_syncs_before_any_real_sync(site):
    aws = FakeAws()
    main([*site, "--yes"], runner=aws, ask=no_prompt)
    dry, real = aws.syncs(dryrun=True), aws.syncs(dryrun=False)
    assert [[a for a in c if a != "--dryrun"] for c in dry] == real
    last_dry = max(aws.commands.index(c) for c in dry)
    assert all(aws.commands.index(c) > last_dry for c in real)


def test_dry_run_summary_counts_uploads_once_and_lists_every_delete(site, capsys):
    main([*site, "--dry-run"], runner=FakeAws(), ask=no_prompt)
    out = capsys.readouterr().out
    assert "3 to upload" in out and "2 to delete" in out
    assert "s3://rosart-site/_astro/old.Zz99.css" in out
    assert "s3://rosart-site/old-page/index.html" in out


def test_dry_run_flag_never_syncs_for_real_or_invalidates(site, capsys):
    aws = FakeAws()
    assert main([*site, "--dry-run"], runner=aws, ask=no_prompt) == 0
    assert aws.syncs(dryrun=True) and not aws.syncs(dryrun=False)
    assert not aws.ran("cloudfront")
    assert "nothing was uploaded" in capsys.readouterr().out


@pytest.mark.parametrize("answer", ["", "n", "no", "y", "YES please"])
def test_anything_but_yes_at_the_prompt_stops_before_the_real_sync(site, answer, capsys):
    aws = FakeAws()
    assert main(site, runner=aws, ask=lambda question: answer) == 1
    assert aws.syncs(dryrun=True) and not aws.syncs(dryrun=False)
    assert not aws.ran("cloudfront")
    assert "cancelled" in capsys.readouterr().err


def test_no_terminal_and_no_yes_flag_refuses(site, capsys):
    def closed_stdin(question):
        raise EOFError

    aws = FakeAws()
    assert main(site, runner=aws, ask=closed_stdin) == 1
    assert not aws.syncs(dryrun=False) and not aws.ran("cloudfront")
    assert "--yes" in capsys.readouterr().err


def test_yes_at_the_prompt_publishes_and_shows_the_target(site):
    questions = []
    aws = FakeAws()
    assert main(site, runner=aws, ask=lambda q: questions.append(q) or "yes") == 0
    assert len(questions) == 1
    assert "s3://rosart-site" in questions[0] and "123456789012" in questions[0]
    assert len(aws.syncs(dryrun=False)) == 6


def test_invalidation_is_everything_on_the_configured_distribution(site, capsys):
    aws = FakeAws()
    main([*site, "--yes"], runner=aws, ask=no_prompt)
    [invalidation] = aws.ran("cloudfront", "create-invalidation")
    assert option(invalidation, "--distribution-id") == "E2ABCDEF123456"
    assert option(invalidation, "--paths") == "/*"
    assert aws.commands.index(invalidation) > max(aws.commands.index(c) for c in aws.syncs(dryrun=False))
    out = capsys.readouterr().out
    assert "I2EXAMPLE" in out and "get-invalidation" in out
    assert not aws.ran("cloudfront", "wait")


def test_wait_flag_waits_for_the_invalidation(site):
    aws = FakeAws()
    assert main([*site, "--yes", "--wait"], runner=aws, ask=no_prompt) == 0
    [wait] = aws.ran("cloudfront", "wait", "invalidation-completed")
    assert option(wait, "--id") == "I2EXAMPLE" and option(wait, "--distribution-id") == "E2ABCDEF123456"


def test_failed_identity_check_stops_before_any_sync(site, capsys):
    aws = FakeAws(fail=lambda c: c[1] == "sts")
    assert main([*site, "--yes"], runner=aws, ask=no_prompt) == 1
    assert not aws.ran("s3") and not aws.ran("cloudfront")
    assert "AccessDenied" in capsys.readouterr().err


def test_failed_dry_run_stops_before_the_real_sync(site):
    aws = FakeAws(fail=lambda c: "--dryrun" in c)
    assert main([*site, "--yes"], runner=aws, ask=no_prompt) == 1
    assert not aws.syncs(dryrun=False) and not aws.ran("cloudfront")


@pytest.mark.parametrize("failing_pass", [0, 1, 2, 3, 4, 5])
def test_failed_real_sync_stops_the_flow_without_invalidating(site, failing_pass, capsys):
    real_seen = []

    def fail(command):
        if command[1:3] == ["s3", "sync"] and "--dryrun" not in command:
            real_seen.append(command)
            return len(real_seen) - 1 == failing_pass
        return False

    aws = FakeAws(fail=fail)
    assert main([*site, "--yes"], runner=aws, ask=no_prompt) == 1
    assert len(aws.syncs(dryrun=False)) == failing_pass + 1
    assert not aws.ran("cloudfront")
    err = capsys.readouterr().err
    assert "AccessDenied" in err and "not invalidated" in err


def test_real_runner_uses_only_the_profile_for_credentials(monkeypatch):
    """The subprocess seam: env keys or a default profile must not override the named profile."""
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE",
                 "AWS_DEFAULT_PROFILE"):
        monkeypatch.setenv(name, "from-env")
    monkeypatch.setenv("AWS_PAGER", "less")
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="{}", stderr="")

    runner = AwsCliRunner(executable="C:/aws/aws.exe", run=fake_run)
    result = runner(["aws", "sts", "get-caller-identity", "--profile", "rosart-deploy"])
    [(command, kwargs)] = calls
    assert command == ["C:/aws/aws.exe", "sts", "get-caller-identity", "--profile", "rosart-deploy"]
    assert not {k for k in kwargs["env"] if k.startswith("AWS_") and k != "AWS_PAGER"}
    assert kwargs["env"]["AWS_PAGER"] == ""
    assert result == AwsResult(0, "{}", "")


def test_missing_aws_cli_is_refused(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(PublishError, match="not on PATH"):
        AwsCliRunner()


def test_failed_invalidation_is_reported(site, capsys):
    aws = FakeAws(fail=lambda c: c[1:3] == ["cloudfront", "create-invalidation"])
    assert main([*site, "--yes"], runner=aws, ask=no_prompt) == 1
    assert "create-invalidation" in capsys.readouterr().err


# --- a simulated bucket: what the syncs leave behind ---------------------------------------

def included(rel, command):
    """The AWS CLI filter rule: everything starts included, filters apply in order, last match wins
    (`*` matches across `/`, as fnmatch does)."""
    keep = True
    for flag, pattern in filters(command):
        if fnmatch.fnmatchcase(rel, pattern):
            keep = flag == "--include"
    return keep


class BucketSim(FakeAws):
    """A fake aws whose `s3 sync` follows the CLI's default comparison against an in-memory bucket:
    upload when the key is missing, the size differs or the local file is newer than the object
    (its LastModified is the upload time), delete unmatched keys that pass the filters. Each object
    keeps the Content-Type and Cache-Control it was uploaded with."""

    def __init__(self, objects=None):
        super().__init__()
        self.objects = dict(objects or {})  # key -> {"size", "modified", "type", "cache"}
        self.uploads: list[str] = []         # keys uploaded for real, once per upload
        self.deletes: list[str] = []

    def sync_output(self, command):
        dryrun = "--dryrun" in command
        source, dest = Path(command[3]), command[4]
        prefix = dest.removeprefix("s3://rosart-site").lstrip("/")
        prefix = f"{prefix}/" if prefix else ""
        local = {p.relative_to(source).as_posix(): p for p in source.rglob("*") if p.is_file()}
        local = {rel: p for rel, p in local.items() if included(rel, command)}
        lines = []
        for rel, path in sorted(local.items()):
            key, stat = prefix + rel, path.stat()
            current = self.objects.get(key)
            if current and current["size"] == stat.st_size and stat.st_mtime <= current["modified"]:
                continue
            lines.append(f"upload: {path} to s3://rosart-site/{key}")
            if not dryrun:
                self.uploads.append(key)
                self.objects[key] = {"size": stat.st_size, "modified": time.time(),
                                     "type": option(command, "--content-type") or mimetypes.guess_type(rel)[0],
                                     "cache": option(command, "--cache-control")}
        if "--delete" in command:
            for key in sorted(k for k in self.objects if k.startswith(prefix)):
                rel = key[len(prefix):]
                if included(rel, command) and rel not in local:
                    lines.append(f"delete: s3://rosart-site/{key}")
                    if not dryrun:
                        self.deletes.append(key)
                        del self.objects[key]
        return "".join(("(dryrun) " if dryrun else "") + line + "\r\n" for line in lines)


def stale_object():
    return {"size": 1, "modified": 0.0, "type": "binary/octet-stream", "cache": None}


@pytest.fixture
def llms_site(site, tmp_path):
    """The site plus an llms.txt with non-ASCII text, as the real build writes it."""
    (tmp_path / "dist" / "llms.txt").write_text("# Rosart\n\n- Soufflé rose\n", encoding="utf-8")
    return site


def test_text_and_xml_land_as_utf8_with_revalidate_caching(llms_site):
    bucket = BucketSim()
    assert main([*llms_site, "--yes"], runner=bucket, ask=no_prompt) == 0
    for key, content_type in [("robots.txt", "text/plain; charset=utf-8"),
                              ("llms.txt", "text/plain; charset=utf-8"),
                              ("sitemap-index.xml", "application/xml; charset=utf-8"),
                              ("index.html", "text/html; charset=utf-8"),
                              ("products/p0/index.html", "text/html; charset=utf-8")]:
        assert bucket.objects[key]["type"] == content_type, key
        assert bucket.objects[key]["cache"] == REVALIDATE, key
    assert bucket.objects["_astro/site.Ab12Cd.css"]["cache"] == IMMUTABLE


def test_typed_files_upload_once_and_keep_their_type_even_when_the_local_clock_runs_ahead(llms_site, tmp_path):
    # With the local clock ahead of S3, every local file looks newer than its fresh object, so any
    # later pass that included a typed file would upload it again with the CLI's guessed type.
    future = time.time() + 3600
    for path in (tmp_path / "dist").rglob("*"):
        if path.is_file():
            os.utime(path, (future, future))
    bucket = BucketSim()
    assert main([*llms_site, "--yes"], runner=bucket, ask=no_prompt) == 0
    for key in ("robots.txt", "llms.txt", "sitemap-index.xml", "index.html"):
        assert bucket.uploads.count(key) == 1, key
    assert bucket.objects["robots.txt"]["type"] == "text/plain; charset=utf-8"
    assert bucket.objects["sitemap-index.xml"]["type"] == "application/xml; charset=utf-8"
    assert bucket.objects["index.html"]["type"] == "text/html; charset=utf-8"


def test_publish_mirrors_dist_exactly_deleting_stale_keys_of_every_type(llms_site, tmp_path):
    stale = ["old.txt", "old/sitemap-0.xml", "old-page/index.html", "feed.json", "_astro/old.Zz99.css"]
    bucket = BucketSim({key: stale_object() for key in stale})
    assert main([*llms_site, "--yes"], runner=bucket, ask=no_prompt) == 0
    dist = tmp_path / "dist"
    assert set(bucket.objects) == {p.relative_to(dist).as_posix() for p in dist.rglob("*") if p.is_file()}
    assert sorted(bucket.deletes) == sorted(stale)


def test_republishing_an_unchanged_dist_uploads_and_deletes_nothing(llms_site):
    first = BucketSim()
    assert main([*llms_site, "--yes"], runner=first, ask=no_prompt) == 0
    again = BucketSim(first.objects)
    assert main([*llms_site, "--yes"], runner=again, ask=no_prompt) == 0
    assert again.uploads == [] and again.deletes == []
    assert again.objects == first.objects


def test_dry_run_leaves_the_bucket_untouched(llms_site, capsys):
    bucket = BucketSim({"old.txt": stale_object()})
    assert main([*llms_site, "--dry-run"], runner=bucket, ask=no_prompt) == 0
    assert set(bucket.objects) == {"old.txt"} and bucket.uploads == []
    assert "delete s3://rosart-site/old.txt" in capsys.readouterr().out
