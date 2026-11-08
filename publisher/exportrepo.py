"""The export repository: a plain bare git repository, one commit per publish (spec 006 T060; FR-012, E5).

``export/`` of each published snapshot is committed here: the person is the author, ``rosart-publisher`` the
committer. The repository has no remote and the service never pushes: ``git`` below refuses ``push``,
``remote``, ``fetch``, ``clone`` and ``submodule``. A private index file per operation (``GIT_INDEX_FILE``) and
``GIT_WORK_TREE`` pointing at a snapshot (or the image) mean the bare repository needs no work tree of its own
(research R-9). The history holds everything under ``export/`` except ``export/theme/`` (Shopify-licensed
theme code) and ``*.part``; it includes ``export/theme-content.json`` (C3/E10).

Every summary compares against HEAD, the last successfully committed export. A commit that failed after a
good publish is *pending*: ``retry_pending`` makes it at the start of the next job, oldest first.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional
from zoneinfo import ZoneInfo

import export_changes
from export_changes import ChangesError, ExportChanges, commit_message

log = logging.getLogger("rosart.exportrepo")

COMMITTER_NAME = "rosart-publisher"
COMMITTER_EMAIL = "rosart-publisher@fbsrv"
FORBIDDEN_COMMANDS = ("push", "remote", "fetch", "clone", "submodule")
GIT_TIMEOUT_S = 300
# What a commit leaves out: the raw theme code and half-written downloads.
PATHSPEC = ("export", ":(exclude)export/theme", ":(exclude)*.part")
OPTIONS_WITH_VALUE = ("-c", "-C", "--git-dir", "--work-tree")


class ExportRepoError(RuntimeError):
    """An export repository operation failed; the message is plain words, never a secret."""


def _subcommand(args: tuple[str, ...]) -> str:
    skip = False
    for arg in args:
        if skip:
            skip = False
        elif arg in OPTIONS_WITH_VALUE:
            skip = True
        elif not arg.startswith("-"):
            return arg
    return ""


class ExportRepo:
    def __init__(self, path: Path, children: Any, env: Mapping[str, str], tmp_dir: Optional[Path],
                 tz_display: str = "Australia/Melbourne") -> None:
        self.path = Path(path)
        self._children = children
        self._env = dict(env)
        self._tmp = Path(tmp_dir) if tmp_dir else None
        self._tz = ZoneInfo(tz_display)

    # --- git ----------------------------------------------------------------------------

    def git(self, *args: str, env_extra: Optional[Mapping[str, str]] = None, cwd: Optional[Path] = None,
            check: bool = True):
        """Run git against the repository through the supervised child runner."""
        command = _subcommand(args)
        if command in FORBIDDEN_COMMANDS:
            raise ExportRepoError(f"git {command} is not allowed: the export repository never talks to another repository")
        env = {**self._env, "GIT_DIR": str(self.path), "GIT_TERMINAL_PROMPT": "0", **(env_extra or {})}
        done = self._children.run(["git", *args], env=env, cwd=cwd, timeout_mono=self._children.mono() + GIT_TIMEOUT_S)
        if check and done.returncode != 0:
            raise ExportRepoError(f"git {command} failed: {(done.stderr.strip() or done.stdout.strip())[-300:]}")
        return done

    def is_empty(self) -> bool:
        return self.git("rev-parse", "--verify", "-q", "HEAD", check=False).returncode != 0

    def _scratch(self) -> Path:
        base = self._tmp or Path(".")
        folder = base / f"export-repo-{uuid.uuid4().hex[:12]}"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    # --- seed ---------------------------------------------------------------------------

    def seed(self, image_root: Path, code_commit: str, when: Optional[datetime] = None) -> str:
        """The first commit, from the image's committed ``export/``; refuses a repository that has history."""
        if not self.is_empty():
            raise ExportRepoError("the export repository is not empty: seed-export-repo only seeds an empty one")
        message = f"Seed the export history from rosart-web {code_commit}\n"
        return self._commit(Path(image_root), message, COMMITTER_NAME, COMMITTER_EMAIL,
                            when or datetime.now(timezone.utc))

    # --- summary ------------------------------------------------------------------------

    def summary(self, snapshot_dir: Path) -> ExportChanges:
        """What the snapshot's ``export/`` changes against HEAD (private index, the snapshot as work tree)."""
        if self.is_empty():
            raise ExportRepoError("the export repository has no commit to compare against")
        scratch = self._scratch()
        try:
            env = {"GIT_WORK_TREE": str(snapshot_dir), "GIT_INDEX_FILE": str(scratch / "index")}
            self.git("read-tree", "HEAD", env_extra=env, cwd=snapshot_dir)
            return export_changes.summarise(Path(snapshot_dir), git=self._git_for(env, Path(snapshot_dir)))
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    def _git_for(self, env: Mapping[str, str], cwd: Path) -> Callable[..., subprocess.CompletedProcess]:
        """The ``git`` hook of ``export_changes``: the same commands, run through this wrapper."""
        def run(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
            done = self.git(*args, env_extra=env, cwd=cwd, check=False)
            if check and done.returncode != 0:
                raise ChangesError(f"git {' '.join(args[:2])} failed: {done.stderr.strip()[-400:]}")
            return subprocess.CompletedProcess(list(args), done.returncode, done.stdout.encode("utf-8"),
                                               done.stderr.encode("utf-8"))
        return run

    # --- commit -------------------------------------------------------------------------

    def commit(self, snapshot_dir: Path, changes: ExportChanges, *, person_name: str, person_email: str,
               when: datetime, job_id: int, snapshot_id: str, code_commit: str,
               invalidation_id: Optional[str]) -> str:
        """One commit holding the snapshot's ``export/``; returns its hash."""
        melbourne_day = when.astimezone(self._tz).date()
        message = commit_message(changes, melbourne_day, person_name)
        message += (f"\nJob: {job_id}\nSnapshot: {snapshot_id}\nCode: {code_commit}\n"
                    f"Invalidation: {invalidation_id or 'none'}\n")
        return self._commit(Path(snapshot_dir), message, person_name, person_email, when)

    def _commit(self, work_tree: Path, message: str, author_name: str, author_email: str, when: datetime) -> str:
        scratch = self._scratch()
        try:
            stamp = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+0000")
            env = {"GIT_WORK_TREE": str(work_tree), "GIT_INDEX_FILE": str(scratch / "index"),
                   "GIT_AUTHOR_NAME": author_name, "GIT_AUTHOR_EMAIL": author_email, "GIT_AUTHOR_DATE": stamp,
                   "GIT_COMMITTER_NAME": COMMITTER_NAME, "GIT_COMMITTER_EMAIL": COMMITTER_EMAIL,
                   "GIT_COMMITTER_DATE": stamp}
            if not self.is_empty():
                self.git("read-tree", "HEAD", env_extra=env, cwd=work_tree)
            self.git("add", "-A", "--", *PATHSPEC, env_extra=env, cwd=work_tree)
            message_file = scratch / "message.txt"
            message_file.write_bytes(message.encode("utf-8"))
            self.git("-c", "commit.gpgsign=false", "commit", "-q", "--allow-empty", "-F", str(message_file),
                     env_extra=env, cwd=work_tree)
            return self.git("rev-parse", "HEAD").stdout.strip()
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    # --- pending commits (E5) -----------------------------------------------------------

    def retry_pending(self, store: Any, snapshots: Any) -> list[int]:
        """Commit every pending publish, oldest first, one commit each; stops (and logs) at the first failure."""
        done: list[int] = []
        for job in store.pending_export_commits():
            folder = snapshots.folder(job.snapshot_id)
            try:
                changes = self.summary(folder)
                sha = self.commit(folder, changes, person_name=job.person_name or job.person_user,
                                  person_email=job.person_email or f"{job.person_user}@rosart-publisher.invalid",
                                  when=datetime.fromisoformat(job.finished_at), job_id=job.id,
                                  snapshot_id=job.snapshot_id, code_commit=job.code_commit,
                                  invalidation_id=job.invalidation_id)
            except (ExportRepoError, ChangesError, OSError, ValueError) as exc:
                log.warning("the history commit of job %s is still pending: %s", job.id, exc)
                store.update(job.id, export_commit_error=plain_commit_error(exc))
                break
            store.update(job.id, export_commit=sha, export_commit_error=None)
            done.append(job.id)
        return done

    def last_recorded_date(self) -> Optional[datetime]:
        if self.is_empty():
            return None
        stamp = self.git("log", "-1", "--format=%cI", "HEAD").stdout.strip()
        return datetime.fromisoformat(stamp).astimezone(self._tz)

    def summary_label(self, store: Any) -> Optional[str]:
        """"since the last recorded publish <date>" while a commit is pending, else None (FR-012, SC-007)."""
        if not store.pending_export_commits():
            return None
        last = self.last_recorded_date()
        return f"since the last recorded publish {last.day} {last:%B %Y}" if last else "since the last recorded publish"


def plain_commit_error(exc: BaseException) -> str:
    return f"the export history commit failed ({type(exc).__name__}): {str(exc)[:200]}"
