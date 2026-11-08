"""Start-up recovery: a restart never leaves a child writing, a lock held, or a success shown (spec 006 T090; E2).

Run once by ``serve`` before the server accepts a request. In this order: kill the leftover child process group of
the job left ``running`` (only when the leader's start time still matches the stored one, so a reused PID is never
killed) and wait until it is gone; only then write the job ``interrupted``, which releases the single-job lock;
remove orphan snapshot folders, stale workspace copies and job records past retention; and announce every job
whose Slack notice is still pending (``notice_state`` makes a second start-up post nothing).
"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from publisher.runner import kill_and_wait
from publisher.snapshots import canonical_json
from publisher.translate import interrupted_failure

log = logging.getLogger("rosart.recovery")


@dataclass
class Report:
    interrupted: list[int] = field(default_factory=list)
    notices: list[threading.Thread] = field(default_factory=list)


def recover(*, store: Any, snapshots: Any, workspace: Any, config: Any, notifier: Optional[Any] = None,
            ops: Optional[Any] = None, log_for: Optional[Callable[[Any], Callable[[str], None]]] = None) -> Report:
    """Recover from a restart; ``ChildStuck`` from an unkillable group propagates (the lock must not release early)."""
    report = Report()
    job = store.running()
    if job is not None:
        if job.child_pgid is not None:
            log.warning("job %s left a child process group %s running: stopping it", job.id, job.child_pgid)
            kill_and_wait(job.child_pgid, job.child_started, ops=ops)
            store.clear_child(job.id)
        failure = interrupted_failure(job.kind)
        written = store.finish(job.id, "interrupted", reason_code=failure["code"], reason_text=failure["text"],
                               failures_json=canonical_json([failure]), notice_state="pending")
        if written:
            report.interrupted.append(job.id)
            log.warning("job %s (%s) was left running by a restart: marked interrupted", job.id, job.kind)
    workspace.cleanup()
    snapshots.cleanup_orphans()
    store.purge(config.retention_days, config.data)
    if notifier is not None:
        for pending in store.pending_notices():
            say = log_for(pending) if log_for else None
            report.notices.append(notifier.notify_async(pending.id, log=say))
    return report
