"""The job store: SQLite in WAL mode, the single-running-job lock, the final-state write (spec 006 T042).

One file holds the ``jobs`` and ``snapshots`` tables (data-model.md). A job starts only inside
``BEGIN IMMEDIATE`` (read the running row, refuse if there is one, insert), with the ``one_running``
unique index as the backstop. The lock is released by the final-state UPDATE, which is written once
(``WHERE state = 'running'``); a second write is a logged no-op (FR-009, FR-010). Every row carries
the site id (FR-019) and a person (SC-003).
"""
from __future__ import annotations

import logging
import sqlite3
from contextlib import closing, contextmanager
from dataclasses import dataclass, fields
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterator, Optional

log = logging.getLogger("rosart.jobs")

CHECK_FINAL_STATES = ("passed", "failed", "interrupted")
PUBLISH_FINAL_STATES = ("published", "published-cache-pending", "failed", "publish-failed-partial", "interrupted")
FINAL_STATES = {"check": CHECK_FINAL_STATES, "publish": PUBLISH_FINAL_STATES}
ALL_STATES = ("running",) + tuple(dict.fromkeys(CHECK_FINAL_STATES + PUBLISH_FINAL_STATES))

# Columns a caller may set after the start, besides the state itself.
UPDATABLE = ("phase", "passes_started", "snapshot_id", "reason_code", "reason_text", "failures_json", "summary_json",
             "invalidation_id", "export_commit", "export_commit_error", "notice_state")

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    site TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('check', 'publish')),
    state TEXT NOT NULL CHECK (state IN ({", ".join(repr(s) for s in ALL_STATES)})),
    phase TEXT,
    passes_started INTEGER NOT NULL DEFAULT 0 CHECK (passes_started BETWEEN 0 AND 6),
    person_user TEXT NOT NULL CHECK (person_user <> ''),
    person_name TEXT,
    person_email TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    deadline_at TEXT,
    child_pgid INTEGER,
    child_started INTEGER,
    snapshot_id TEXT,
    reason_code TEXT,
    reason_text TEXT,
    failures_json TEXT,
    summary_json TEXT,
    invalidation_id TEXT,
    export_commit TEXT,
    export_commit_error TEXT,
    code_commit TEXT NOT NULL,
    notice_state TEXT NOT NULL DEFAULT 'not-required'
        CHECK (notice_state IN ('not-required', 'pending', 'posted', 'failed')),
    log_path TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS one_running ON jobs(site) WHERE state = 'running';
CREATE INDEX IF NOT EXISTS jobs_by_site_finished ON jobs(site, kind, finished_at);
CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY,
    site TEXT NOT NULL,
    check_job_id INTEGER NOT NULL REFERENCES jobs(id),
    created_at TEXT NOT NULL,
    window_end TEXT NOT NULL,
    created_mono REAL NOT NULL,
    boot_id TEXT NOT NULL,
    marker_json TEXT,
    content_hash TEXT NOT NULL,
    plan_json TEXT NOT NULL,
    summary_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'kept' CHECK (status IN ('kept', 'published', 'stale', 'deleted')),
    published_by_job_id INTEGER REFERENCES jobs(id)
);
"""


@dataclass(frozen=True)
class Job:
    id: int
    site: str
    kind: str
    state: str
    phase: Optional[str]
    passes_started: int
    person_user: str
    person_name: Optional[str]
    person_email: Optional[str]
    created_at: str
    started_at: Optional[str]
    finished_at: Optional[str]
    deadline_at: Optional[str]
    child_pgid: Optional[int]
    child_started: Optional[int]
    snapshot_id: Optional[str]
    reason_code: Optional[str]
    reason_text: Optional[str]
    failures_json: Optional[str]
    summary_json: Optional[str]
    invalidation_id: Optional[str]
    export_commit: Optional[str]
    export_commit_error: Optional[str]
    code_commit: str
    notice_state: str
    log_path: Optional[str]


JOB_COLUMNS = tuple(f.name for f in fields(Job))


class JobAlreadyRunning(Exception):
    """A job is running for the site; ``running`` is its row (FR-009)."""

    def __init__(self, running: Job) -> None:
        super().__init__(f"job {running.id} ({running.kind}) is already running")
        self.running = running


class JobStore:
    def __init__(self, db_path: Path, site: str, clock: Any) -> None:
        self.db_path = Path(db_path)
        self.site = site
        self._clock = clock
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._open()) as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)

    def _open(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=30000")
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        """A short-lived connection (autocommit; use explicit transactions)."""
        with closing(self._open()) as db:
            yield db

    def _now(self) -> str:
        return self._clock.utc_now().isoformat()

    @staticmethod
    def _job(row: Optional[sqlite3.Row]) -> Optional[Job]:
        return Job(**{name: row[name] for name in JOB_COLUMNS}) if row is not None else None

    def get(self, job_id: int) -> Optional[Job]:
        with self.connection() as db:
            return self._job(db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())

    def running(self) -> Optional[Job]:
        with self.connection() as db:
            return self._job(db.execute("SELECT * FROM jobs WHERE site = ? AND state = 'running'",
                                        (self.site,)).fetchone())

    def start_job(self, kind: str, person_user: Optional[str], person_name: Optional[str], person_email: Optional[str],
                  code_commit: str, timeout_s: int) -> Job:
        """Start a job inside BEGIN IMMEDIATE; raise :class:`JobAlreadyRunning` when one runs."""
        if kind not in FINAL_STATES:
            raise ValueError(f"unknown job kind {kind!r}")
        if not (person_user or "").strip():
            raise ValueError("a job needs a person (SC-003)")
        now = self._clock.utc_now()
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                existing = db.execute("SELECT * FROM jobs WHERE site = ? AND state = 'running'", (self.site,)).fetchone()
                if existing is not None:
                    raise JobAlreadyRunning(self._job(existing))
                cursor = db.execute(
                    "INSERT INTO jobs (site, kind, state, person_user, person_name, person_email, created_at, "
                    "started_at, deadline_at, code_commit) VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?, ?)",
                    (self.site, kind, person_user.strip(), person_name or "", person_email or "", now.isoformat(),
                     now.isoformat(), (now + timedelta(seconds=timeout_s)).isoformat(), code_commit))
                job_id = cursor.lastrowid
                db.execute("UPDATE jobs SET log_path = ? WHERE id = ?", (f"logs/{self.site}/{job_id}.log", job_id))
                row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
                db.execute("COMMIT")
            except BaseException:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        return self._job(row)

    @staticmethod
    def _checked(values: dict[str, Any]) -> dict[str, Any]:
        for name in values:
            if name not in UPDATABLE:
                raise ValueError(f"{name} cannot be set on a job after it starts")
        return values

    def update(self, job_id: int, **values: Any) -> None:
        """Set progress fields (phase, passes_started, snapshot, notice state, export commit...)."""
        values = self._checked(values)
        if not values:
            return
        assignments = ", ".join(f"{name} = ?" for name in values)
        with self.connection() as db:
            db.execute(f"UPDATE jobs SET {assignments} WHERE id = ?", (*values.values(), job_id))

    def finish(self, job_id: int, state: str, **values: Any) -> bool:
        """Write the final state once. Returns False (and logs) when the job already ended."""
        values = self._checked(values)
        job = self.get(job_id)
        kind = job.kind if job else None
        if kind is not None and state not in FINAL_STATES[kind]:
            raise ValueError(f"state {state!r} is not a final state of a {kind} job")
        if kind is None and state not in ALL_STATES[1:]:
            raise ValueError(f"state {state!r} is not a final state")
        assignments = ", ".join(["state = ?", "finished_at = ?", *(f"{name} = ?" for name in values)])
        with self.connection() as db:
            cursor = db.execute(f"UPDATE jobs SET {assignments} WHERE id = ? AND state = 'running'",
                                (state, self._now(), *values.values(), job_id))
            if cursor.rowcount == 1:
                return True
        log.warning("job %s already finished (or unknown): ignoring a second write of state %s", job_id, state)
        return False

    def pending_export_commits(self) -> list[Job]:
        """Published jobs whose export commit failed, oldest first (E5): retried before any new job."""
        with self.connection() as db:
            rows = db.execute("SELECT * FROM jobs WHERE site = ? AND kind = 'publish' AND export_commit IS NULL "
                              "AND export_commit_error IS NOT NULL AND state IN ('published', 'published-cache-pending') "
                              "ORDER BY id", (self.site,)).fetchall()
        return [self._job(row) for row in rows]

    def set_child(self, job_id: int, pgid: int, started: Optional[int]) -> None:
        with self.connection() as db:
            db.execute("UPDATE jobs SET child_pgid = ?, child_started = ? WHERE id = ?", (pgid, started, job_id))

    def clear_child(self, job_id: int) -> None:
        with self.connection() as db:
            db.execute("UPDATE jobs SET child_pgid = NULL, child_started = NULL WHERE id = ?", (job_id,))
