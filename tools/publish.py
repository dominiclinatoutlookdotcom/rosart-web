"""Publish the built site in ``dist/`` to the private S3 bucket behind CloudFront.

Shopify stays the content editor: ``make publish`` re-exports, rebuilds and checks the site, then
runs this script, which syncs ``dist/`` to the bucket and invalidates the distribution. All AWS
access goes through the AWS CLI with the named profile from ``deploy.config.json`` (default
``rosart-deploy``); this script never reads credential files or keys itself.

Usage::

    python tools/publish.py [--config FILE] [--dist DIR] [--dry-run] [--yes] [--wait] [--check-config]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "deploy.config.json"
DEFAULT_PROFILE = "rosart-deploy"
PLACEHOLDER = "REPLACE_ME"

# Field name in deploy.config.json -> (pattern, what a valid value looks like).
FIELDS = {
    "profile": (r"[A-Za-z0-9_+=,.@-]+", "an AWS CLI profile name"),
    "bucket": (r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", "an S3 bucket name (not a URL)"),
    "region": (r"[a-z]{2}(-gov)?-[a-z]+-\d+", "an AWS region such as ap-southeast-2"),
    "distributionId": (r"[A-Z0-9]{8,20}", "a CloudFront distribution ID such as E2ABCDEF123456"),
}


class PublishError(RuntimeError):
    """A refusal or failure that stops the publish; the message says what to do."""


@dataclass(frozen=True)
class DeployConfig:
    profile: str
    bucket: str
    region: str
    distribution_id: str

    @property
    def bucket_url(self) -> str:
        return f"s3://{self.bucket}"


def load_config(path: Path) -> DeployConfig:
    """Read and validate the deploy config; refuse placeholders, malformed values and unknown keys."""
    path = Path(path)
    if not path.is_file():
        raise PublishError(f"no deploy config at {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PublishError(f"{path.name} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise PublishError(f"{path.name} must hold a JSON object")

    unknown = sorted(k for k in raw if k not in FIELDS and not k.startswith("_"))
    if unknown:
        raise PublishError(f"{path.name} has unknown keys: {', '.join(unknown)} "
                           f"(allowed: {', '.join(FIELDS)}; never put credentials here)")

    values = {name: str(raw.get(name) or "").strip() for name in FIELDS}
    values["profile"] = values["profile"] or DEFAULT_PROFILE
    unfilled = [name for name, value in values.items() if not value or PLACEHOLDER in value]
    if unfilled:
        raise PublishError(
            f"{path.name} is not filled in: {', '.join(unfilled)}. Set them once the bucket and "
            "CloudFront distribution exist (README, section Publish); nothing was published.")
    for name, (pattern, meaning) in FIELDS.items():
        if not re.fullmatch(pattern, values[name]):
            raise PublishError(f"{path.name}: {name} {values[name]!r} is not {meaning}")
    return DeployConfig(values["profile"], values["bucket"], values["region"], values["distributionId"])


# --- dist preflight -----------------------------------------------------------------------

# The sync deletes whatever the bucket holds that dist/ lacks, so an empty or half-built dist/
# would wipe the live site. These are the floor a real build always clears (it has 21 pages).
REQUIRED_FILES = ("index.html", "404.html", "robots.txt", "sitemap-index.xml")
MIN_PAGES = 10
ASSETS_DIR = "_astro"


@dataclass(frozen=True)
class DistSummary:
    pages: int
    files: int
    bytes: int


def check_dist(dist: Path) -> DistSummary:
    """Refuse a missing, empty or incomplete build; return its page and file counts."""
    dist = Path(dist)
    if not dist.is_dir():
        raise PublishError(f"{dist} does not exist: build the site first (make check)")
    for name in REQUIRED_FILES:
        path = dist / name
        if not path.is_file() or path.stat().st_size == 0:
            raise PublishError(f"{dist} has no {name} (or it is empty): refusing to publish an incomplete build")
    assets = dist / ASSETS_DIR
    if not assets.is_dir() or not any(p.is_file() for p in assets.rglob("*")):
        raise PublishError(f"{dist} has no built assets in {ASSETS_DIR}/: refusing to publish an incomplete build")
    files = [p for p in dist.rglob("*") if p.is_file()]
    pages = sum(1 for p in files if p.name == "index.html")
    if pages < MIN_PAGES:
        raise PublishError(f"{dist} has only {pages} pages (index.html files), fewer than {MIN_PAGES}: "
                           "refusing to publish what looks like a broken build")
    return DistSummary(pages=pages, files=len(files), bytes=sum(p.stat().st_size for p in files))


# --- command guard ------------------------------------------------------------------------

# The only operations publishing needs, as the leading words after `aws`.
ALLOWED_OPERATIONS = (
    ("sts", "get-caller-identity"),
    ("s3", "sync"),
    ("cloudfront", "create-invalidation"),
    ("cloudfront", "wait", "invalidation-completed"),
)


def _option_values(args: list[str], option: str) -> list[str]:
    return [args[i + 1] if i + 1 < len(args) else "" for i, arg in enumerate(args) if arg == option]


def assert_command_allowed(command: list[str], config: DeployConfig) -> None:
    """Refuse any aws command that is not a publish operation on the configured profile, bucket
    and distribution. Every command passes through here before it runs."""
    if not command or command[0] != "aws":
        raise PublishError(f"not an aws command: {command!r}")
    args = command[1:]
    if not any(tuple(args[:len(op)]) == op for op in ALLOWED_OPERATIONS):
        raise PublishError(f"aws {' '.join(args[:2])} is not allowed: publishing only syncs, "
                           "invalidates and checks the caller identity")

    profiles = _option_values(args, "--profile")
    if profiles != [config.profile] or any(a.startswith("--profile=") for a in args):
        raise PublishError(f"every aws command must pass --profile {config.profile} exactly once, "
                           f"got {profiles or 'none'}: {' '.join(command)}")

    for arg in args:
        if arg.lower().startswith("s3://"):
            target = arg[len("s3://"):]
            if target != config.bucket and not target.startswith(config.bucket + "/"):
                raise PublishError(f"{arg} is outside the configured bucket {config.bucket_url}")

    if args[:2] == ["s3", "sync"]:
        positional = args[2:4]
        if len(positional) < 2 or positional[0].lower().startswith("s3://") \
                or not positional[1].lower().startswith("s3://"):
            raise PublishError(f"s3 sync must upload from a local folder to {config.bucket_url}: "
                               f"{' '.join(command)}")

    if args[0] == "cloudfront":
        ids = _option_values(args, "--distribution-id")
        if ids != [config.distribution_id]:
            raise PublishError(f"cloudfront commands must target --distribution-id "
                               f"{config.distribution_id}, got {ids or 'none'}")


# --- running aws --------------------------------------------------------------------------

@dataclass(frozen=True)
class AwsResult:
    returncode: int
    stdout: str
    stderr: str


Runner = Callable[[list[str]], AwsResult]
Ask = Callable[[str], str]

# Removed from the CLI's environment so the named profile is its only source of credentials.
STRIPPED_ENV = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
                "AWS_PROFILE", "AWS_DEFAULT_PROFILE")


class AwsCliRunner:
    """Runs a guarded ``["aws", ...]`` command with the real AWS CLI and captures its output."""

    def __init__(self, executable: Optional[str] = None, timeout: int = 3600,
                 run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
        self.executable = executable or shutil.which("aws")
        if not self.executable:
            raise PublishError("the aws CLI is not on PATH: install AWS CLI v2 (README, section Publish)")
        self.timeout = timeout
        self._run = run

    def __call__(self, command: list[str]) -> AwsResult:
        env = {k: v for k, v in os.environ.items() if k.upper() not in STRIPPED_ENV}
        env["AWS_PAGER"] = ""
        try:
            completed = self._run([self.executable, *command[1:]], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", env=env, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise PublishError(f"aws {' '.join(command[1:3])} timed out after {self.timeout}s") from exc
        return AwsResult(completed.returncode, completed.stdout or "", completed.stderr or "")


class Aws:
    """Adds the profile and region to every command, guards it, runs it and fails on a non-zero exit."""

    def __init__(self, config: DeployConfig, runner: Runner) -> None:
        self.config = config
        self.runner = runner

    def run(self, args: list[str]) -> AwsResult:
        command = ["aws", *args, "--profile", self.config.profile, "--region", self.config.region,
                   "--no-cli-pager"]
        if args[0] != "s3":
            command += ["--output", "json"]
        assert_command_allowed(command, self.config)
        result = self.runner(command)
        if result.returncode != 0:
            detail = (result.stderr.strip() or result.stdout.strip() or "no output")[-1500:]
            raise PublishError(f"aws {' '.join(args[:3])} failed (exit {result.returncode}): {detail}")
        return result

    def json(self, args: list[str]) -> dict:
        result = self.run(args)
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise PublishError(f"aws {' '.join(args[:2])} printed no JSON: {result.stdout[:300]!r}") from exc


# --- the sync plan ------------------------------------------------------------------------

IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "public, max-age=0, must-revalidate"
HTML_TYPE = "text/html; charset=utf-8"
SYNC_LINE = re.compile(r"^(?:\(dryrun\) )?(upload|delete): (.+)$")


def sync_passes(config: DeployConfig, dist: Path) -> list[list[str]]:
    """The syncs, in the order they run.

    ``_astro/`` holds content-hashed assets (cached for a year); everything else is revalidated on
    every request. Assets are synced as their own folder to their own prefix, so their ``--delete``
    can only reach ``_astro/``; the root passes end their filters with ``--exclude _astro/*``
    (last match wins), so they never upload or delete under ``_astro/``. New assets go up first and
    old ones are pruned last, so no page ever points at an asset that is not in the bucket.
    """
    assets = [str(Path(dist) / ASSETS_DIR), f"{config.bucket_url}/{ASSETS_DIR}"]
    root = [str(Path(dist)), config.bucket_url]
    not_assets = ["--exclude", f"{ASSETS_DIR}/*"]
    return [
        ["s3", "sync", *assets, "--cache-control", IMMUTABLE, "--no-progress"],
        # HTML on its own pass so its type is text/html; utf-8 whatever the local MIME map says.
        ["s3", "sync", *root, "--exclude", "*", "--include", "*.html", *not_assets,
         "--content-type", HTML_TYPE, "--cache-control", REVALIDATE, "--no-progress"],
        ["s3", "sync", *root, "--delete", *not_assets, "--cache-control", REVALIDATE, "--no-progress"],
        ["s3", "sync", *assets, "--delete", "--cache-control", IMMUTABLE, "--no-progress"],
    ]


def parse_sync_output(stdout: str) -> tuple[list[str], list[str]]:
    """``(uploaded keys, deleted keys)`` as s3:// URLs, from `aws s3 sync` output (dry run or real)."""
    uploads: list[str] = []
    deletes: list[str] = []
    for line in stdout.splitlines():
        match = SYNC_LINE.match(line.strip())
        if not match:
            continue
        if match[1] == "upload":
            uploads.append(match[2].rsplit(" to ", 1)[-1])
        else:
            deletes.append(match[2])
    return uploads, deletes


# --- the flow -----------------------------------------------------------------------------

def show_identity(aws: Aws) -> dict:
    identity = aws.json(["sts", "get-caller-identity"])
    print(f"AWS identity: account {identity.get('Account')}, {identity.get('Arn')} "
          f"(profile {aws.config.profile})")
    return identity


def plan(aws: Aws, passes: list[list[str]]) -> None:
    """Dry-run every pass and print what the real sync would upload and delete."""
    uploads: dict[str, None] = {}
    deletes: dict[str, None] = {}
    for args in passes:
        up, down = parse_sync_output(aws.run([*args, "--dryrun"]).stdout)
        uploads.update(dict.fromkeys(up))
        deletes.update(dict.fromkeys(down))
    print(f"Dry run for {aws.config.bucket_url}: {len(uploads)} to upload, {len(deletes)} to delete")
    for key in deletes:
        print(f"  delete {key}")


def confirm(aws: Aws, identity: dict, ask: Ask) -> None:
    question = (f"Publish to {aws.config.bucket_url} in account {identity.get('Account')} "
                f"({identity.get('Arn')}) and invalidate CloudFront {aws.config.distribution_id}? "
                "Type yes to continue: ")
    try:
        answer = ask(question)
    except EOFError:
        raise PublishError("no terminal to confirm on: run with --yes to publish without the prompt; "
                           "nothing was uploaded") from None
    if answer.strip() != "yes":
        raise PublishError("cancelled: nothing was uploaded")


def sync(aws: Aws, passes: list[list[str]]) -> None:
    for number, args in enumerate(passes, start=1):
        try:
            up, down = parse_sync_output(aws.run(args).stdout)
        except PublishError as exc:
            raise PublishError(f"{exc}\nsync {number}/{len(passes)} failed: the bucket may be partly "
                               "updated and CloudFront was not invalidated. Fix the cause and publish "
                               "again.") from None
        print(f"sync {number}/{len(passes)} {args[3]}: {len(up)} uploaded, {len(down)} deleted")


def invalidate(aws: Aws, wait: bool) -> None:
    distribution = aws.config.distribution_id
    try:
        response = aws.json(["cloudfront", "create-invalidation", "--distribution-id", distribution,
                             "--paths", "/*"])
    except PublishError as exc:
        raise PublishError(f"{exc}\nThe bucket is updated but the cache was not invalidated: run "
                           f"aws cloudfront create-invalidation --distribution-id {distribution} "
                           f"--paths \"/*\" --profile {aws.config.profile}") from None
    invalidation = response.get("Invalidation") or {}
    invalidation_id = invalidation.get("Id")
    print(f"CloudFront invalidation {invalidation_id} for /* on {distribution}: {invalidation.get('Status')}")
    if wait:
        print("waiting for the invalidation to complete...")
        aws.run(["cloudfront", "wait", "invalidation-completed", "--distribution-id", distribution,
                 "--id", str(invalidation_id)])
        print("invalidation complete: the site is live")
    else:
        print(f"check it with: aws cloudfront get-invalidation --distribution-id {distribution} "
              f"--id {invalidation_id} --profile {aws.config.profile}")


def publish(config: DeployConfig, dist: Path, runner: Runner, ask: Ask, *, yes: bool,
            dry_run_only: bool, wait: bool) -> None:
    aws = Aws(config, runner)
    identity = show_identity(aws)
    passes = sync_passes(config, dist)
    plan(aws, passes)
    if dry_run_only:
        print("dry run only: nothing was uploaded, nothing was invalidated")
        return
    if not yes:
        confirm(aws, identity, ask)
    sync(aws, passes)
    invalidate(aws, wait)


def main(argv: Optional[list[str]] = None, runner: Optional[Runner] = None, ask: Ask = input) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--dist", type=Path, default=REPO_ROOT / "dist")
    parser.add_argument("--check-config", action="store_true", help="validate the deploy config only; call nothing")
    parser.add_argument("--dry-run", action="store_true", help="stop after the dry run: never upload or invalidate")
    parser.add_argument("--yes", action="store_true", help="publish without the confirmation prompt")
    parser.add_argument("--wait", action="store_true", help="wait for the CloudFront invalidation to complete")
    args = parser.parse_args(argv)

    try:
        config = load_config(args.config)
        if args.check_config:
            print(f"deploy config OK: profile {config.profile}, {config.bucket_url} ({config.region}), "
                  f"CloudFront {config.distribution_id}")
            return 0
        summary = check_dist(args.dist)
        print(f"dist: {summary.pages} pages, {summary.files} files, {summary.bytes / 1024 / 1024:.1f} MB")
        publish(config, args.dist, runner or AwsCliRunner(), ask,
                yes=args.yes, dry_run_only=args.dry_run, wait=args.wait)
    except PublishError as exc:
        print(f"publish: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    # Line-buffered so progress shows up in order when make or a pipe holds stdout.
    sys.stdout.reconfigure(line_buffering=True)
    sys.exit(main())
