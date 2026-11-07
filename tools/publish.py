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
# The only IAM identity allowed to publish (spec 006 Clarifications 2026-11-07): the owner's
# `rosart-deploy` CLI profile and the box's key both belong to this user.
DEPLOY_USER = "rosart-web-deploy"
MARKER_KEY = "_publish.json"

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
    # The bucket marker, and only that one key (see _assert_marker_command).
    ("s3api", "get-object"),
    ("s3api", "put-object"),
    ("cloudfront", "create-invalidation"),
    ("cloudfront", "wait", "invalidation-completed"),
)


def _option_values(args: list[str], option: str) -> list[str]:
    return [args[i + 1] if i + 1 < len(args) else "" for i, arg in enumerate(args) if arg == option]


def _inside(path: str, folder: Path) -> bool:
    try:
        Path(path).resolve().relative_to(Path(folder).resolve())
    except ValueError:
        return False
    return True


def _assert_marker_command(args: list[str], config: DeployConfig, temp_dir: Optional[Path]) -> None:
    """The two s3api shapes: get or put of exactly the marker key in the configured bucket, with a
    local file inside ``temp_dir``, and no option beyond the ones the shape needs."""
    operation = args[1]
    if temp_dir is None:
        raise PublishError(f"s3api {operation} needs a temp folder for its local file, none was given")
    valued = {"--bucket", "--key", "--profile", "--region", "--output"}
    if operation == "put-object":
        valued |= {"--body", "--content-type", "--cache-control"}
    seen: dict[str, list[str]] = {}
    positional: list[str] = []
    i = 2
    while i < len(args):
        arg = args[i]
        if arg == "--no-cli-pager":
            i += 1
        elif arg in valued:
            seen.setdefault(arg, []).append(args[i + 1] if i + 1 < len(args) else "")
            i += 2
        elif arg.startswith("--"):
            raise PublishError(f"{arg} is not allowed on s3api {operation}")
        else:
            positional.append(arg)
            i += 1
    if seen.get("--bucket") != [config.bucket]:
        raise PublishError(f"s3api {operation} must use --bucket {config.bucket}, got "
                           f"{seen.get('--bucket') or 'none'}")
    if seen.get("--key") != [MARKER_KEY]:
        raise PublishError(f"s3api {operation} may only touch --key {MARKER_KEY}, got "
                           f"{seen.get('--key') or 'none'}")
    if operation == "put-object":
        expected = {"--content-type": "application/json", "--cache-control": "no-store"}
        for option, value in expected.items():
            if seen.get(option) != [value]:
                raise PublishError(f"s3api put-object must pass {option} {value}, got "
                                   f"{seen.get(option) or 'none'}")
        local = seen.get("--body", [])
        if positional or len(local) != 1:
            raise PublishError("s3api put-object takes exactly one --body file and no other argument")
    else:
        local = positional
        if len(local) != 1:
            raise PublishError("s3api get-object takes exactly one local file to write")
    if not _inside(local[0], temp_dir):
        raise PublishError(f"the local file {local[0]} is not inside the temp folder {temp_dir}")


def assert_command_allowed(command: list[str], config: DeployConfig,
                           temp_dir: Optional[Path] = None) -> None:
    """Refuse any aws command that is not a publish operation on the configured profile, bucket
    and distribution. Every command passes through here before it runs. ``temp_dir`` is the only
    folder the bucket-marker commands may read or write locally."""
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

    if args[0] == "s3api":
        _assert_marker_command(args, config, temp_dir)

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

    def __init__(self, config: DeployConfig, runner: Runner, temp_dir: Optional[Path] = None) -> None:
        self.config = config
        self.runner = runner
        self.temp_dir = temp_dir

    def run(self, args: list[str], temp_dir: Optional[Path] = None) -> AwsResult:
        command = ["aws", *args, "--profile", self.config.profile, "--region", self.config.region,
                   "--no-cli-pager"]
        if args[0] != "s3":
            command += ["--output", "json"]
        assert_command_allowed(command, self.config, temp_dir or self.temp_dir)
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
# Root files whose type is set explicitly, so browsers decode them as UTF-8 (llms.txt holds
# non-ASCII text) whatever the local MIME map guesses: pattern -> Content-Type.
TYPED_FILES = (
    ("*.html", HTML_TYPE),
    ("*.txt", "text/plain; charset=utf-8"),
    ("*.xml", "application/xml; charset=utf-8"),
)
SYNC_LINE = re.compile(r"^(?:\(dryrun\) )?(upload|delete): (.+)$")


def sync_passes(config: DeployConfig, dist: Path) -> list[list[str]]:
    """The syncs, in the order they run.

    ``_astro/`` holds content-hashed assets (cached for a year); everything else is revalidated on
    every request. Assets are synced as their own folder to their own prefix, so their ``--delete``
    can only reach ``_astro/``; the root passes end their filters with ``--exclude _astro/*``
    (last match wins), so they never upload or delete under ``_astro/``. New assets go up first and
    old ones are pruned last, so no page ever points at an asset that is not in the bucket.

    Each typed root pass owns its pattern outright: it uploads and deletes only those files, and
    the general root pass excludes them. A general pass that still included them would upload them
    again, with the CLI's guessed type, whenever the local clock runs ahead of S3 (the sync re-uploads
    any file newer than its object), so the union of the root passes mirrors ``dist/`` with each
    file uploaded by exactly one pass.
    """
    assets = [str(Path(dist) / ASSETS_DIR), f"{config.bucket_url}/{ASSETS_DIR}"]
    root = [str(Path(dist)), config.bucket_url]
    not_assets = ["--exclude", f"{ASSETS_DIR}/*"]
    # The bucket marker is written after the last pass and must survive every --delete.
    keep_marker = ["--exclude", MARKER_KEY]
    typed = [["s3", "sync", *root, "--delete", "--exclude", "*", "--include", pattern, *keep_marker,
              *not_assets,
              "--content-type", content_type, "--cache-control", REVALIDATE, "--no-progress"]
             for pattern, content_type in TYPED_FILES]
    not_typed = [arg for pattern, _ in TYPED_FILES for arg in ("--exclude", pattern)]
    return [
        ["s3", "sync", *assets, "--cache-control", IMMUTABLE, "--no-progress"],
        *typed,
        ["s3", "sync", *root, "--delete", *not_typed, *keep_marker, *not_assets, "--cache-control",
         REVALIDATE, "--no-progress"],
        ["s3", "sync", *assets, "--delete", *keep_marker, "--cache-control", IMMUTABLE, "--no-progress"],
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
    if not re.fullmatch(rf"arn:aws:iam::\d{{12}}:user/{re.escape(DEPLOY_USER)}", str(identity.get("Arn"))):
        raise PublishError(f"the profile {aws.config.profile} is {identity.get('Arn')}, not the IAM user "
                           f"{DEPLOY_USER}: fix the profile's key (README, section Publish); "
                           "nothing was uploaded")
    return identity


@dataclass(frozen=True)
class Plan:
    """What the dry run says the real passes would do, as s3:// URLs."""
    uploads: set[str]
    deletes: set[str]


def plan(aws: Aws, passes: list[list[str]]) -> Plan:
    """Dry-run every pass, print what the real sync would upload and delete, and return it."""
    uploads: dict[str, None] = {}
    deletes: dict[str, None] = {}
    for args in passes:
        up, down = parse_sync_output(aws.run([*args, "--dryrun"]).stdout)
        uploads.update(dict.fromkeys(up))
        deletes.update(dict.fromkeys(down))
    print(f"Dry run for {aws.config.bucket_url}: {len(uploads)} to upload, {len(deletes)} to delete")
    for key in deletes:
        print(f"  delete {key}")
    return Plan(set(uploads), set(deletes))


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


def sync(aws: Aws, passes: list[list[str]], on_pass: Optional[Callable[[int], None]] = None) -> None:
    for number, args in enumerate(passes, start=1):
        if on_pass:
            on_pass(number)
        try:
            up, down = parse_sync_output(aws.run(args).stdout)
        except PublishError as exc:
            raise PublishError(f"{exc}\nsync {number}/{len(passes)} failed: the bucket may be partly "
                               "updated and CloudFront was not invalidated. Fix the cause and publish "
                               "again.") from None
        print(f"sync {number}/{len(passes)} {args[3]}: {len(up)} uploaded, {len(down)} deleted")


def invalidate(aws: Aws, wait: bool) -> tuple[Optional[str], bool]:
    """Invalidate everything; return ``(invalidation id, completed)``. A waiter that fails after a
    good create does not raise: the invalidation is under way, so ``completed`` is False."""
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
        try:
            aws.run(["cloudfront", "wait", "invalidation-completed", "--distribution-id", distribution,
                     "--id", str(invalidation_id)])
        except PublishError as exc:
            print(f"publish: {exc}", file=sys.stderr)
            return invalidation_id, False
        print("invalidation complete: the site is live")
        return invalidation_id, True
    print(f"check it with: aws cloudfront get-invalidation --distribution-id {distribution} "
          f"--id {invalidation_id} --profile {aws.config.profile}")
    return invalidation_id, False


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
    invalidation_id, completed = invalidate(aws, wait)
    if wait and not completed:
        raise PublishError(f"the invalidation {invalidation_id} was created but the wait for it failed: "
                           "it may still complete (the site is updated)")


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
