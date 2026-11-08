"""The check and publish jobs as step functions (spec 006 R-3; plan "Check job" and "Publish job").

``run_check(job, deps)`` runs the check phases in order and ends the job ``passed`` (with a checked snapshot) or
``failed``. Every collaborator comes in through ``Deps``, so the flow runs against fakes; the real objects are
wired in ``publisher.__main__``. A step function never posts to Slack and never raises for an expected failure:
it writes the final state (after every child process group is gone) and returns an ``Outcome``; the job worker
announces it.
"""
from __future__ import annotations

import contextlib
import errno
import io
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

from publisher.exportrepo import ExportRepoError
from publisher.runner import ChildTimeout
from publisher.snapshots import canonical_json
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
AWS_PHASES = ("aws-identity", "marker-read", "dry-run")
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
    """An expected failure with its plain-words form (the translator of T072 refines the text)."""

    def __init__(self, code: str, text: str, action: str = "contact-owner", key_line: Optional[str] = None,
                 where: Optional[str] = None) -> None:
        super().__init__(text)
        self.code, self.text, self.action, self.key_line, self.where = code, text, action, key_line, where


class _LogWriter(io.TextIOBase):
    """Where the tools' ``print`` output goes during a step: the job's scrubbed log."""

    def __init__(self, job_log: Any) -> None:
        self._log = job_log

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        self._log.write(text)
        return len(text)


def _key_line(output: str) -> str:
    lines = [line.strip() for line in output.splitlines() if line.strip()]
    return (lines[-1] if lines else "")[:KEY_LINE_MAX]


def failure_from(exc: BaseException, phase: str, scrubber: Any) -> dict[str, Any]:
    """The failure record (code, plain text, staff action) for an exception raised in ``phase``."""
    if isinstance(exc, StepFailure):
        failure = {"code": exc.code, "text": exc.text, "action": exc.action}
        if exc.key_line:
            failure["key_line"] = exc.key_line
        if exc.where:
            failure["where"] = exc.where
    else:
        code = getattr(exc, "code", None)
        if isinstance(exc, OSError) and exc.errno == errno.ENOSPC:
            code = "disk-full"
        elif isinstance(exc, ChildTimeout):
            code = "deadline"
        elif isinstance(exc, publish.PublishError) and phase in AWS_PHASES:
            code = "aws-before-upload"
        failure = {"code": code if code in FAILURE_CODES else "unknown", "text": str(exc) or type(exc).__name__,
                   "action": "check-again" if code == "deadline" else "contact-owner"}
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
        failure = failure_from(exc, track["phase"], deps.scrubber)
        deps.log.line(f"check failed in phase {track['phase']}: {deps.scrubber.describe_exception(exc)}")
        log.info("job %s: check failed in phase %s (%s)", job.id, track["phase"], failure["code"])
        deps.store.finish(job.id, "failed", reason_code=failure["code"], reason_text=failure["text"],
                          failures_json=canonical_json([failure]), notice_state="pending")
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
        raise StepFailure("unknown", f"{' '.join(argv)} failed (exit {done.returncode})",
                          key_line=_key_line(done.stdout + "\n" + done.stderr))


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
