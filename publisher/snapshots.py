"""Checked snapshots: hash, the publishable predicate, retention, orphans (spec 006 T058; data-model).

A passed check leaves a folder ``snapshots/<site>/<snapshot id>/`` (``dist/``, ``export/``, ``.gitattributes``,
``snapshot.json``) and a row. The folder is ``<check job id>.partial`` until the row is written. A snapshot is
*publishable* when it is ``kept``, its check is the newest passed check of the site (a later failed or
interrupted check does not supersede it, E3), it is inside its 60 minute window on the monotonic clock of the
same boot (E9), and no job is running. One function decides that, for the page and for the publish handler.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, fields
from datetime import timedelta
from pathlib import Path
from typing import Any, Iterator, Optional

log = logging.getLogger("rosart.snapshots")

PUBLISHED_STATES = ("published", "published-cache-pending")


def canonical_json(value: Any) -> str:
    """Sorted keys, no spaces: the form hashed and compared everywhere (snapshot, marker)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def file_lines(dist: Path) -> str:
    """Sorted ``<sha256 of file>  <relative path>`` lines of every file under ``dist``."""
    dist = Path(dist)
    entries = []
    for path in dist.rglob("*"):
        if not path.is_file():
            continue
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        entries.append((path.relative_to(dist).as_posix(), digest.hexdigest()))
    return "".join(f"{digest}  {rel}\n" for rel, digest in sorted(entries))


def content_hash(dist: Path, plan: dict) -> str:
    """SHA-256 over the file lines of ``dist`` then the canonical JSON of the dry-run plan (FR-008)."""
    return hashlib.sha256((file_lines(dist) + canonical_json(plan)).encode("utf-8")).hexdigest()


def snapshot_id(site: str, check_job_id: int, digest: str) -> str:
    return f"{site}-{check_job_id}-{digest[:12]}"


@dataclass(frozen=True)
class Snapshot:
    id: str
    site: str
    check_job_id: int
    created_at: str
    window_end: str
    created_mono: float
    boot_id: str
    marker_json: Optional[str]
    content_hash: str
    plan_json: str
    summary_json: str
    status: str
    published_by_job_id: Optional[int]


SNAPSHOT_COLUMNS = tuple(f.name for f in fields(Snapshot))


class SnapshotStore:
    def __init__(self, store: Any, data_dir: Path, site: str, clock: Any, window_s: int) -> None:
        self.store = store
        self.data_dir = Path(data_dir)
        self.site = site
        self.clock = clock
        self.window_s = window_s

    # --- folders ------------------------------------------------------------------------

    @property
    def root(self) -> Path:
        return self.data_dir / "snapshots" / self.site

    def folder(self, snapshot: str) -> Path:
        return self.root / snapshot

    def partial_folder(self, job_id: int) -> Path:
        return self.root / f"{job_id}.partial"

    # --- rows ---------------------------------------------------------------------------

    @staticmethod
    def _snapshot(row: Optional[sqlite3.Row]) -> Optional[Snapshot]:
        return Snapshot(**{name: row[name] for name in SNAPSHOT_COLUMNS}) if row is not None else None

    def get(self, snapshot: str) -> Optional[Snapshot]:
        with self.store.connection() as db:
            return self._snapshot(db.execute("SELECT * FROM snapshots WHERE id = ? AND site = ?",
                                             (snapshot, self.site)).fetchone())

    def record(self, check_job_id: int, plan: dict, summary: dict, marker_json: Optional[str]) -> Snapshot:
        """Hash the check's ``.partial`` folder, rename it to its id, and write the row (check phase 11)."""
        partial = self.partial_folder(check_job_id)
        digest = content_hash(partial / "dist", plan)
        sid = snapshot_id(self.site, check_job_id, digest)
        target = self.folder(sid)
        shutil.rmtree(target, ignore_errors=True)
        os.replace(partial, target)
        now = self.clock.utc_now()
        job = self.store.get(check_job_id)
        (target / "snapshot.json").write_text(json.dumps({
            "id": sid, "site": self.site, "check_job_id": check_job_id, "content_hash": digest,
            "code_commit": job.code_commit if job else None, "plan_json": plan}, indent=2, sort_keys=True),
            encoding="utf-8")
        with self.store.connection() as db:
            db.execute(
                "INSERT INTO snapshots (id, site, check_job_id, created_at, window_end, created_mono, boot_id, "
                "marker_json, content_hash, plan_json, summary_json, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'kept')",
                (sid, self.site, check_job_id, now.isoformat(), (now + timedelta(seconds=self.window_s)).isoformat(),
                 self.clock.mono(), self.clock.boot_id(), marker_json, digest, canonical_json(plan),
                 canonical_json(summary)))
        return self.get(sid)

    def set_status(self, snapshot: str, status: str, published_by_job_id: Optional[int] = None) -> None:
        with self.store.connection() as db:
            db.execute("UPDATE snapshots SET status = ?, published_by_job_id = COALESCE(?, published_by_job_id) "
                       "WHERE id = ? AND site = ?", (status, published_by_job_id, snapshot, self.site))

    # --- the predicate ------------------------------------------------------------------

    def publishable(self, snapshot: str, db: Optional[sqlite3.Connection] = None) -> bool:
        """The one rule for "can this snapshot be published now" (the page and the publish handler share it)."""
        with self._connection(db) as conn:
            row = conn.execute("SELECT * FROM snapshots WHERE id = ? AND site = ?", (snapshot, self.site)).fetchone()
            snap = self._snapshot(row)
            if snap is None or snap.status != "kept":
                return False
            if snap.boot_id != self.clock.boot_id():
                return False
            if not 0 <= self.clock.mono() - snap.created_mono < self.window_s:
                return False
            newest = conn.execute("SELECT MAX(id) FROM jobs WHERE site = ? AND kind = 'check' AND state = 'passed'",
                                  (self.site,)).fetchone()[0]
            if newest != snap.check_job_id:
                return False
            return conn.execute("SELECT 1 FROM jobs WHERE site = ? AND state = 'running'", (self.site,)).fetchone() is None

    def current_publishable(self) -> Optional[Snapshot]:
        """The snapshot the page offers: the newest kept one, when it is publishable."""
        with self.store.connection() as db:
            row = db.execute("SELECT id FROM snapshots WHERE site = ? AND status = 'kept' "
                             "ORDER BY check_job_id DESC LIMIT 1", (self.site,)).fetchone()
            if row is None or not self.publishable(row["id"], db):
                return None
        return self.get(row["id"])

    @contextmanager
    def _connection(self, db: Optional[sqlite3.Connection]) -> Iterator[sqlite3.Connection]:
        if db is not None:
            yield db
            return
        with self.store.connection() as conn:
            yield conn

    # --- retention and orphans ----------------------------------------------------------

    def retain(self) -> list[str]:
        """Keep the newest passed check's snapshot, the last published one and any with a pending export commit."""
        with self.store.connection() as db:
            keep: set[str] = set()
            newest = db.execute("SELECT id FROM snapshots WHERE site = ? AND check_job_id = "
                                "(SELECT MAX(id) FROM jobs WHERE site = ? AND kind = 'check' AND state = 'passed')",
                                (self.site, self.site)).fetchone()
            if newest:
                keep.add(newest["id"])
            last = db.execute("SELECT id FROM snapshots WHERE site = ? AND status = 'published' "
                              "ORDER BY published_by_job_id DESC LIMIT 1", (self.site,)).fetchone()
            if last:
                keep.add(last["id"])
            keep.update(r["snapshot_id"] for r in db.execute(
                "SELECT snapshot_id FROM jobs WHERE site = ? AND kind = 'publish' AND snapshot_id IS NOT NULL "
                "AND state IN ('published', 'published-cache-pending') AND export_commit IS NULL "
                "AND export_commit_error IS NOT NULL", (self.site,)))
            doomed = [r["id"] for r in db.execute("SELECT id FROM snapshots WHERE site = ? AND status <> 'deleted'",
                                                  (self.site,)) if r["id"] not in keep]
            for sid in doomed:
                shutil.rmtree(self.folder(sid), ignore_errors=True)
                db.execute("UPDATE snapshots SET status = 'deleted' WHERE id = ?", (sid,))
        return doomed

    def cleanup_orphans(self) -> None:
        """Start-up: remove ``.partial`` folders and every folder no live snapshot row points to."""
        if not self.root.is_dir():
            return
        with self.store.connection() as db:
            live = {r["id"] for r in db.execute("SELECT id FROM snapshots WHERE site = ? AND status <> 'deleted'",
                                                (self.site,))}
        for entry in self.root.iterdir():
            if entry.name.endswith(".partial") or entry.name not in live:
                log.info("removing the snapshot folder %s (no snapshot row points to it)", entry.name)
                shutil.rmtree(entry, ignore_errors=True)
