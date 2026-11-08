"""The check and publish jobs as step functions (spec 006 R-3; plan "Check job" and "Publish job").

``run_check(job, deps)`` runs the check phases in order and ends the job ``passed`` (with a checked snapshot) or
``failed``. Every collaborator comes in through ``Deps``, so the flow runs against fakes; the real objects are
wired in ``publisher.__main__``. A step function never posts to Slack and never raises for an expected failure:
it writes the final state (after every child process group is gone) and returns an ``Outcome``; the job worker
announces it.
"""
from __future__ import annotations

import contextlib
import io
import json
import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

import export_changes
import publish
from export_shopify import run_export
from pull_theme import DEFAULT_THEME
from theme_admin import read_theme

from publisher import translate
from publisher.counts import change_counts
from publisher.exportrepo import ExportRepoError, plain_commit_error
from publisher.runner import ChildTimeout
from publisher.snapshots import canonical_json, content_hash
from publisher.workspace import WorkspaceError, clean_build

log = logging.getLogger("rosart.steps")

# The commands of the Makefile's ``check:`` recipe and its ``export`` variables (research R-6). The
# drift test (tests/publisher/test_makefile_drift.py) parses the Makefile and fails when they differ.
CHECK_STEPS: tuple[tuple[str, ...], ...] = (
    ("npm", "run", "build"),
    ("npm", "test"),
    ("npm", "run", "typecheck"),
    ("npm", "run", "check:dist"),
)
CHECK_ENV: dict[str, str] = {"ASTRO_TELEMETRY_DISABLED": "1"}
CHECK_STEP_PHASES = ("build", "test", "typecheck", "check-dist")

FAILURE_CODES = frozenset({
    "email-rule", "collection-collision", "broken-reference", "theme-fidelity", "image-too-large", "shopify-access",
    "shopify-not-permitted", "shopify-write-scope", "theme-missing", "theme-not-live", "theme-checksum",
    "egress-refused", "aws-before-upload", "aws-identity-mismatch", "new-deletions", "snapshot-hash",
    "snapshot-stale", "site-changed", "partial-sync", "disk-full", "deadline", "interrupted", "worker-died",
    "unknown"})
DEPLOY_CONFIG = "deploy.config.json"
KEY_LINE_MAX = 500


@dataclass
class Deps:
    """Everything a job needs, injected: the real objects in the service, fakes in the tests."""
    config: Any
    store: Any
    snapshots: Any
    clock: Any
    children: Any                       # ChildRunner (or a fake): run(argv, env, cwd, timeout_mono), mono()
    log: Any                            # JobLog
    scrubber: Any
    workspace: Any
    export_repo: Any
    code_commit: str
    make_shopify: Callable[[float], tuple[Any, Any]]       # deadline (monotonic) -> (token source, AdminApiRunner)
    fetch: Callable[[str], bytes]                          # the egress-checked downloader
    make_aws: Callable[[Any, Path, Optional[float]], Any]  # (DeployConfig, temp folder, timeout) -> publish.Aws
    theme_id: str = DEFAULT_THEME
    read_theme: Callable[..., Any] = read_theme
    run_export: Callable[..., Any] = run_export
    sleep: Optional[Callable[[float], None]] = None

    def sleeper(self) -> Callable[[float], None]:
        return self.sleep or self.clock.sleep

    def child_env(self) -> dict[str, str]:
        return {**self.config.child_env(), **CHECK_ENV}


@dataclass(frozen=True)
class Outcome:
    state: str
    job_id: int
    snapshot_id: Optional[str] = None
    reason_code: Optional[str] = None
    reason_text: Optional[str] = None


class StepFailure(Exception):
    """An expected failure with its plain-words form (``failures`` carries a translated list, T072)."""

    def __init__(self, code: str, text: str, action: str = "contact-owner", key_line: Optional[str] = None,
                 where: Optional[str] = None, failures: Optional[list[dict[str, Any]]] = None) -> None:
        super().__init__(text)
        self.code, self.text, self.action, self.key_line, self.where = code, text, action, key_line, where
        self.failures = failures        # several translated failures (a failed check step), first one is the main


class _LogWriter(io.TextIOBase):
    """Where the tools' ``print`` output goes during a step: the job's scrubbed log."""

    def __init__(self, job_log: Any) -> None:
        self._log = job_log

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self._log.write(text)
        return len(text)


def failures_for(exc: BaseException, phase: str, scrubber: Any) -> list[dict[str, Any]]:
    """The failure records (code, plain text, staff action) for an exception raised in ``phase`` (T072)."""
    if isinstance(exc, StepFailure) and exc.failures:
        return [_scrubbed(failure, scrubber) for failure in exc.failures]
    if isinstance(exc, StepFailure):
        failure = {"code": exc.code, "text": exc.text, "action": exc.action}
        if exc.key_line:
            failure["key_line"] = exc.key_line
        if exc.where:
            failure["where"] = exc.where
        return [_scrubbed(failure, scrubber)]
    return [_scrubbed(translate.from_exception(exc, phase, scrubber), scrubber)]


def failure_from(exc: BaseException, phase: str, scrubber: Any) -> dict[str, Any]:
    """The first (main) failure record of :func:`failures_for`."""
    return failures_for(exc, phase, scrubber)[0]


def _scrubbed(failure: dict[str, Any], scrubber: Any) -> dict[str, Any]:
    failure = dict(failure)
    failure["text"] = scrubber.scrub(failure["text"])[:600]
    if "key_line" in failure:
        failure["key_line"] = scrubber.scrub(failure["key_line"])
    return failure


# --- the check job ----------------------------------------------------------------------------------

def run_check(job: Any, deps: Deps) -> Outcome:
    """Run the check phases for ``job`` (already started) and write its final state."""
    track = {"phase": "history-retry"}
    partial = deps.snapshots.partial_folder(job.id)
    temp = deps.config.tmp_dir / "jobs" / str(job.id)
    try:
        temp.mkdir(parents=True, exist_ok=True)
        snapshot, summary = _check_phases(job, deps, temp, track)
    except Exception as exc:  # noqa: BLE001 - every failure is translated and written to the job
        shutil.rmtree(partial, ignore_errors=True)
        failures = failures_for(exc, track["phase"], deps.scrubber)
        failure = failures[0]
        deps.log.line(f"check failed in phase {track['phase']}: {deps.scrubber.describe_exception(exc)}")
        log.info("job %s: check failed in phase %s (%s)", job.id, track["phase"], failure["code"])
        deps.store.finish(job.id, "failed", reason_code=failure["code"], reason_text=failure["text"],
                          failures_json=canonical_json(failures), notice_state="pending")
        return Outcome("failed", job.id, None, failure["code"], failure["text"])
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    deps.store.finish(job.id, "passed", snapshot_id=snapshot.id, summary_json=canonical_json(summary))
    _retain(deps)
    return Outcome("passed", job.id, snapshot.id)


def _retain(deps: Deps) -> None:
    try:
        deps.snapshots.retain()
    except OSError:
        log.exception("snapshot retention failed")


def _phase(deps: Deps, job_id: int, track: dict, name: str) -> None:
    track["phase"] = name
    deps.store.update(job_id, phase=name)
    deps.log.line(f"== {name}")


def _check_phases(job: Any, deps: Deps, temp: Path, track: dict) -> tuple[Any, dict]:
    clock = deps.clock
    deadline = clock.mono() + deps.config.check_timeout_s

    _phase(deps, job.id, track, "history-retry")
    deps.export_repo.retry_pending(deps.store, deps.snapshots)

    _phase(deps, job.id, track, "workspace")
    workspace = deps.workspace.ensure(deps.code_commit, deps.children, deps.child_env(), deadline)

    _phase(deps, job.id, track, "token")
    tokens, runner = deps.make_shopify(deadline)
    tokens.get()                                    # grants and checks the scopes before anything is read

    export_dir = workspace / "export"
    theme_dir = export_dir / "theme"
    _phase(deps, job.id, track, "theme")
    deps.read_theme(runner, deps.fetch, deps.theme_id, theme_dir)

    _phase(deps, job.id, track, "export")
    try:
        deps.run_export(runner, deps.fetch, deps.sleeper(), export_dir, theme_dir, download=True,
                        log=lambda message: deps.log.line(f"export: {message}"))
    except SystemExit as exc:                       # run_export's own refusal (no theme to read)
        raise StepFailure("unknown", str(exc.code or "the export stopped")) from None

    _phase(deps, job.id, track, "config")
    deploy = publish.load_config(workspace / DEPLOY_CONFIG)

    clean_build(workspace)
    for argv, name in zip(CHECK_STEPS, CHECK_STEP_PHASES):
        _phase(deps, job.id, track, name)
        _run_check_step(deps, argv, workspace, deadline)

    _phase(deps, job.id, track, "snapshot")
    partial = deps.snapshots.partial_folder(job.id)
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir(parents=True)
    shutil.move(str(workspace / "dist"), str(partial / "dist"))
    _copy_export(export_dir, partial / "export")
    if (workspace / ".gitattributes").is_file():
        shutil.copy2(workspace / ".gitattributes", partial / ".gitattributes")

    _phase(deps, job.id, track, "preflight")
    publish.check_dist(partial / "dist")

    aws = deps.make_aws(deploy, temp, None)
    _phase(deps, job.id, track, "aws-identity")
    check_identity(aws)
    _phase(deps, job.id, track, "marker-read")
    marker = publish.read_marker(aws)
    marker_json = canonical_json(marker) if marker is not None else None
    _phase(deps, job.id, track, "dry-run")
    plan = dry_run_plan(deps, aws, deploy, partial / "dist")

    _phase(deps, job.id, track, "summary")
    summary = build_summary(deps, partial, plan)

    _phase(deps, job.id, track, "verify")
    snapshot = deps.snapshots.record(job.id, plan, summary, marker_json)
    return snapshot, summary


def _run_check_step(deps: Deps, argv: tuple[str, ...], workspace: Path, deadline: float) -> None:
    deps.log.line("$ " + " ".join(argv))
    try:
        done = deps.children.run(list(argv), env=deps.child_env(), cwd=workspace, timeout_mono=deadline)
    except ChildTimeout:
        raise StepFailure("deadline", "The check took too long and was stopped; check again, and tell the owner "
                                      "if it keeps happening", "check-again") from None
    deps.log.write(done.stdout)
    deps.log.write(done.stderr)
    if done.returncode != 0:
        failures = translate.from_output(done.stdout + "\n" + done.stderr, deps.scrubber)
        raise StepFailure(failures[0]["code"], failures[0]["text"], failures[0]["action"],
                          key_line=failures[0].get("key_line"), failures=failures)


def _copy_export(source: Path, dest: Path) -> None:
    """``export/`` without the raw theme code and without half-written downloads."""
    def ignore(folder: str, names: list[str]) -> list[str]:
        skipped = [name for name in names if name.endswith(".part")]
        if Path(folder) == source:
            skipped += [name for name in names if name == "theme"]
        return skipped

    shutil.copytree(source, dest, ignore=ignore)


def check_identity(aws: Any) -> dict:
    """The AWS identity must be the website deploy user (US5; ``publish.DEPLOY_USER``)."""
    identity = aws.json(["sts", "get-caller-identity"])
    arn = str(identity.get("Arn"))
    if not re.fullmatch(rf"arn:aws:iam::\d{{12}}:user/{re.escape(publish.DEPLOY_USER)}", arn):
        raise StepFailure("aws-identity-mismatch", "The website's AWS access is not the deploy user it should be: "
                          "contact the owner (nothing was uploaded)")
    return identity


def _relative_key(url: str, deploy: Any) -> str:
    prefix = deploy.bucket_url + "/"
    return url[len(prefix):] if url.startswith(prefix) else url


def _page_of(key: str) -> Optional[str]:
    if key == "index.html":
        return "/"
    if key.endswith("/index.html"):
        return "/" + key[: -len("index.html")]
    return None


def dry_run_plan(deps: Deps, aws: Any, deploy: Any, dist: Path) -> dict[str, Any]:
    """The dry run of all six passes, as the stored plan (counts, delete keys, deleted pages)."""
    with contextlib.redirect_stdout(_LogWriter(deps.log)):
        plan = publish.plan(aws, publish.sync_passes(deploy, dist))
    deletes = sorted(_relative_key(url, deploy) for url in plan.deletes)
    return {"upload_count": len(plan.uploads), "delete_keys": deletes,
            "deleted_pages": sorted(page for page in map(_page_of, deletes) if page)}


def build_summary(deps: Deps, snapshot_dir: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """The plain-words summary shown with the Publish button (FR-006), against the export repository's HEAD."""
    changes = deps.export_repo.summary(snapshot_dir)
    text = export_changes.format_summary(changes)
    label = deps.export_repo.summary_label(deps.store)
    if label:
        text = f"Note: this summary is {label}; it may list changes that are already live.\n{text}"
    changed = {kind: {"changed": r.changed, "added": r.added, "removed": r.removed}
               for kind, r in changes.records.items()}
    changed["theme_areas"] = {"changed": list(changes.theme)}
    spot = ["/"] + [url.removeprefix(export_changes.SITE_URL) for url in changes.records["products"].urls]
    return {"text": text, "nothing_new": bool(changes.empty and not plan["delete_keys"]), "changed": changed,
            "images": {"changed": changes.images.changed, "added": changes.images.added,
                       "removed": changes.images.removed},
            "spot_check_urls": spot}


# --- the publish job --------------------------------------------------------------------------------

class NotPublishable(Exception):
    """The snapshot cannot be published now (superseded, expired, published, unknown): check again (FR-007)."""


def start_publish(deps: Deps, snapshot_id: str, person_user: str, person_name: str, person_email: str) -> Any:
    """Start a publish job; the publishable predicate is re-checked inside the start transaction.

    Raises :class:`NotPublishable` or ``JobAlreadyRunning``; nothing is started in either case."""
    def guard(db: Any) -> None:
        if not deps.snapshots.publishable(snapshot_id, db):
            raise NotPublishable(snapshot_id)

    return deps.store.start_job("publish", person_user, person_name, person_email, deps.code_commit,
                                deps.config.publish_timeout_s, snapshot_id=snapshot_id, guard=guard)


def run_publish(job: Any, deps: Deps) -> Outcome:
    """Run the publish phases for ``job`` (already started) and write its final state."""
    track = {"phase": "history-retry"}
    temp = deps.config.tmp_dir / "jobs" / str(job.id)
    try:
        temp.mkdir(parents=True, exist_ok=True)
        result = _publish_phases(job, deps, temp, track)
    except Exception as exc:  # noqa: BLE001 - every failure is translated and written to the job
        failures = failures_for(exc, track["phase"], deps.scrubber)
        failure = failures[0]
        row = deps.store.get(job.id)
        partial = row is not None and row.passes_started >= 1
        state = "publish-failed-partial" if partial else "failed"
        deps.log.line(f"publish failed in phase {track['phase']}: {deps.scrubber.describe_exception(exc)}")
        log.info("job %s: publish ended %s in phase %s (%s)", job.id, state, track["phase"], failure["code"])
        deps.store.finish(job.id, state, reason_code=failure["code"], reason_text=failure["text"],
                          failures_json=canonical_json(failures), notice_state="pending")
        _retain(deps)
        return Outcome(state, job.id, job.snapshot_id, failure["code"], failure["text"])
    finally:
        shutil.rmtree(temp, ignore_errors=True)
    deps.store.finish(job.id, result["state"], invalidation_id=result["invalidation_id"],
                      export_commit=result["export_commit"], export_commit_error=result["export_commit_error"],
                      summary_json=canonical_json(result["summary"]), notice_state="pending")
    _retain(deps)
    return Outcome(result["state"], job.id, job.snapshot_id)


def _publish_phases(job: Any, deps: Deps, temp: Path, track: dict) -> dict[str, Any]:
    _phase(deps, job.id, track, "history-retry")
    deps.export_repo.retry_pending(deps.store, deps.snapshots)

    snapshot = deps.snapshots.get(job.snapshot_id)
    if snapshot is None:
        raise StepFailure("snapshot-stale", "The checked files are gone: run Check again", "check-again")
    folder = deps.snapshots.folder(snapshot.id)
    dist = folder / "dist"
    stored = json.loads(snapshot.plan_json)

    _phase(deps, job.id, track, "verify")
    if content_hash(dist, stored) != snapshot.content_hash:
        raise StepFailure("snapshot-hash", "The checked files changed after the check, so nothing was uploaded: "
                          "run Check again", "check-again")
    _phase(deps, job.id, track, "preflight")
    deploy = publish.load_config(deps.workspace.image_root / DEPLOY_CONFIG)
    publish.check_dist(dist)

    aws = deps.make_aws(deploy, temp, None)
    _phase(deps, job.id, track, "aws-identity")
    check_identity(aws)

    _phase(deps, job.id, track, "marker-read")
    marker = publish.read_marker(aws)
    if (canonical_json(marker) if marker is not None else None) != snapshot.marker_json:
        deps.snapshots.set_status(snapshot.id, "stale")
        raise StepFailure("site-changed", "The live site changed since this check: run Check again "
                          "(nothing was uploaded)", "check-again")

    _phase(deps, job.id, track, "dry-run")
    fresh = dry_run_plan(deps, aws, deploy, dist)
    unexpected = sorted(set(fresh["delete_keys"]) - set(stored["delete_keys"]))
    if unexpected:
        raise StepFailure("new-deletions", "The website's files changed since the check and a publish would now "
                          "delete pages the check did not show: run Check again (nothing was uploaded)",
                          "check-again", key_line=", ".join(unexpected)[:KEY_LINE_MAX])

    def before_pass(number: int) -> None:
        track["phase"] = f"pass-{number}"
        deps.store.update(job.id, passes_started=number, phase=track["phase"])
        deps.log.line(f"== pass-{number}")

    with contextlib.redirect_stdout(_LogWriter(deps.log)):
        publish.sync(aws, publish.sync_passes(deploy, dist), on_pass=before_pass)

    _phase(deps, job.id, track, "marker-write")
    publish.write_marker(aws, publish.build_marker("box", f"box-{job.id}", publish.dist_hash(dist),
                                                   deps.clock.utc_now()))

    _phase(deps, job.id, track, "invalidate")
    with contextlib.redirect_stdout(_LogWriter(deps.log)):
        invalidation_id, _ = publish.invalidate(aws, wait=False)

    _phase(deps, job.id, track, "wait")
    completed = _wait_for_invalidation(deps, deploy, temp, invalidation_id)
    state = "published" if completed else "published-cache-pending"
    deps.snapshots.set_status(snapshot.id, "published", published_by_job_id=job.id)

    _phase(deps, job.id, track, "commit")
    export_commit, export_error = _record_history(deps, job, snapshot, folder, invalidation_id)
    summary = {"uploaded": fresh["upload_count"], "deleted": len(fresh["delete_keys"]),
               "changes": change_counts(json.loads(snapshot.summary_json)), "invalidation_completed": completed}
    return {"state": state, "invalidation_id": invalidation_id, "export_commit": export_commit,
            "export_commit_error": export_error, "summary": summary}


def _wait_for_invalidation(deps: Deps, deploy: Any, temp: Path, invalidation_id: Optional[str]) -> bool:
    """Wait for the CloudFront invalidation, at most ``INVALIDATION_WAIT_S``; a failed wait is not an error."""
    wait_s = deps.config.invalidation_wait_s
    if wait_s <= 0 or not invalidation_id:
        return False
    waiter = deps.make_aws(deploy, temp, float(wait_s))
    try:
        waiter.run(["cloudfront", "wait", "invalidation-completed", "--distribution-id", deploy.distribution_id,
                    "--id", str(invalidation_id)])
    except publish.PublishError as exc:
        deps.log.line(f"the invalidation wait ended without completion: {deps.scrubber.scrub(str(exc))}")
        return False
    return True


def _record_history(deps: Deps, job: Any, snapshot: Any, folder: Path,
                    invalidation_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
    """One history commit of the snapshot's export/; a failure is recorded, never raised (E5)."""
    try:
        changes = deps.export_repo.summary(folder)
        sha = deps.export_repo.commit(
            folder, changes, person_name=job.person_name or job.person_user,
            person_email=job.person_email or f"{job.person_user}@rosart-publisher.invalid",
            when=deps.clock.utc_now(), job_id=job.id, snapshot_id=snapshot.id, code_commit=job.code_commit,
            invalidation_id=invalidation_id)
    except (ExportRepoError, export_changes.ChangesError, OSError, ValueError) as exc:
        deps.log.line(f"the export history commit failed: {deps.scrubber.describe_exception(exc)}")
        return None, plain_commit_error(exc)
    return sha, None
