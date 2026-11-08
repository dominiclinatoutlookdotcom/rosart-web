"""Child processes under supervision: own process group, killed and awaited (spec 006 T044; E2).

Every ``aws``, ``npm``/``node`` and ``git`` child of the service goes through ``ChildRunner.run``. On
POSIX each child leads its own session and process group; the group id and the leader's start time
(``/proc/<pid>/stat`` field 22) are written on the job row before the runner waits, and cleared after
the group is gone. On return, timeout or exception the group is sent SIGTERM, given a grace period,
sent SIGKILL, and awaited (``kill_and_wait``) before control goes back, so the single-job lock is
never released while a child may still be writing. A leader whose start time differs from the stored
one is a reused PID and is left alone. Windows has no process groups: the child is killed alone (the
service itself only ever runs in the Linux image).
"""
from __future__ import annotations

import logging
import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Mapping, Optional, Sequence

log = logging.getLogger("rosart.runner")

SIGTERM = 15
SIGKILL = 9
POLL_S = 0.05
POSIX = os.name == "posix"


class ChildTimeout(Exception):
    """A child ran past its deadline; its group has been killed and awaited."""


class ChildStuck(RuntimeError):
    """A process group would not die after SIGKILL (for example a process in uninterruptible I/O)."""


@dataclass(frozen=True)
class Completed:
    returncode: int
    stdout: str
    stderr: str


def parse_start_time(stat_text: str) -> int:
    """Field 22 (``starttime``) of a ``/proc/<pid>/stat`` line; the command name may hold spaces and brackets."""
    after_name = stat_text[stat_text.rindex(")") + 2:].split()
    return int(after_name[19])          # field 3 is the first after the name, so field 22 is index 19


def proc_start_time(pid: int) -> Optional[int]:
    """The process's start time in clock ticks since boot, or None when it does not exist (or no /proc)."""
    try:
        return parse_start_time(Path(f"/proc/{pid}/stat").read_text(encoding="ascii", errors="replace"))
    except (OSError, ValueError, IndexError):
        return None


class PosixOps:
    """The operating-system calls ``kill_and_wait`` makes; tests substitute fakes."""

    def killpg(self, pgid: int, sig: int) -> None:
        os.killpg(pgid, sig)

    def start_time(self, pid: int) -> Optional[int]:
        return proc_start_time(pid)


def kill_and_wait(pgid: int, started: Optional[int] = None, grace_s: float = 5.0, kill_wait_s: float = 5.0,
                  ops: Optional[Any] = None, sleep: Callable[[float], None] = time.sleep,
                  mono: Callable[[], float] = time.monotonic, reap: Optional[Callable[[], Any]] = None) -> None:
    """SIGTERM the group, wait up to ``grace_s``, SIGKILL, and return only when ``killpg(pgid, 0)`` fails.

    ``started`` is the leader's recorded start time: a leader that exists with another start time is a
    reused PID and nothing is signalled. ``reap`` is called while waiting (``Popen.poll``) so a
    zombie leader of our own child does not keep the group alive.
    """
    ops = ops or PosixOps()
    if started is not None:
        current = ops.start_time(pgid)
        if current is not None and current != started:
            log.warning("process group %s: the leader's start time changed (PID reuse); not killing it", pgid)
            return

    def gone() -> bool:
        if reap is not None:
            reap()
        try:
            ops.killpg(pgid, 0)
        except ProcessLookupError:
            return True
        return False

    def wait_gone(seconds: float) -> bool:
        deadline = mono() + seconds
        while not gone():
            if mono() >= deadline:
                return False
            sleep(POLL_S)
        return True

    try:
        ops.killpg(pgid, SIGTERM)
    except ProcessLookupError:
        return
    if wait_gone(grace_s):
        return
    log.warning("process group %s ignored SIGTERM for %.1f s: sending SIGKILL", pgid, grace_s)
    try:
        ops.killpg(pgid, SIGKILL)
    except ProcessLookupError:
        return
    if not wait_gone(kill_wait_s):
        raise ChildStuck(f"process group {pgid} is still there after SIGKILL")


class NullJobStore:
    """A job store that remembers nothing: for commands that run children outside a job (``seed-export-repo``)."""

    def set_child(self, job_id: int, pgid: int, started: Optional[int]) -> None:
        return None

    def clear_child(self, job_id: int) -> None:
        return None


class ChildRunner:
    """Runs a job's children under supervision; one per running job."""

    def __init__(self, store: Any, job_id: int, clock: Any, scrubber: Optional[Any] = None,
                 ops: Optional[Any] = None) -> None:
        self._store = store
        self._job_id = job_id
        self._clock = clock
        self._scrubber = scrubber
        self._ops = ops or PosixOps()

    def mono(self) -> float:
        return self._clock.mono()

    def run(self, argv: Sequence[str], env: Mapping[str, str], cwd: Optional[Path],
            timeout_mono: Optional[float]) -> Completed:
        """Run ``argv`` with exactly ``env`` and wait; ``timeout_mono`` is an absolute monotonic deadline."""
        remaining = None if timeout_mono is None else max(0.0, timeout_mono - self._clock.mono())
        with tempfile.TemporaryFile() as out, tempfile.TemporaryFile() as err:
            # Output goes to files, not pipes: a grandchild holding a pipe open cannot block the wait.
            proc = subprocess.Popen(list(argv), env=dict(env), cwd=cwd, stdin=subprocess.DEVNULL, stdout=out,
                                    stderr=err, start_new_session=POSIX)
            pgid, started = self._record(proc)
            timed_out = False
            try:
                try:
                    proc.wait(timeout=remaining)
                except subprocess.TimeoutExpired:
                    timed_out = True
            finally:
                self._end(proc, pgid, started)
            out.seek(0)
            err.seek(0)
            stdout, stderr = (self._text(handle.read()) for handle in (out, err))
        if timed_out:
            raise ChildTimeout(f"{Path(argv[0]).name} ran past its deadline and was stopped")
        return Completed(proc.returncode, stdout, stderr)

    def _record(self, proc: subprocess.Popen) -> tuple[Optional[int], Optional[int]]:
        if not POSIX:
            return None, None
        try:
            pgid = os.getpgid(proc.pid)
        except ProcessLookupError:                    # already gone; it was its own group leader
            pgid = proc.pid
        started = self._ops.start_time(proc.pid)
        self._store.set_child(self._job_id, pgid, started)
        return pgid, started

    def _end(self, proc: subprocess.Popen, pgid: Optional[int], started: Optional[int]) -> None:
        try:
            if pgid is not None:
                kill_and_wait(pgid, started, ops=self._ops, reap=proc.poll)
            elif proc.poll() is None:
                proc.kill()
            proc.wait()
        finally:
            if pgid is not None:
                self._store.clear_child(self._job_id)

    def _text(self, data: bytes) -> str:
        text = data.decode("utf-8", errors="replace")
        return self._scrubber.scrub(text) if self._scrubber else text


# --- the job worker ---------------------------------------------------------------------------------

class JobRunner:
    """Starts jobs and runs each on its own worker thread (spec 006 T068).

    ``start_check`` and ``start_publish`` take the single-job lock inside the store's start transaction and
    return the new job row at once; the job then runs on a thread: ``steps.run_check`` or ``steps.run_publish``
    over the ``Deps`` the factory builds for that job (own ``ChildRunner`` and log), and when the final state
    is written the Slack notice is posted on a background thread. Raises ``JobAlreadyRunning`` and, for a
    publish, ``steps.NotPublishable`` (nothing is started in either case).
    """

    def __init__(self, store: Any, snapshots: Any, deps_factory: Callable[[Any], Any], notifier: Any, config: Any,
                 code_commit: str, scrubber: Any) -> None:
        self._store = store
        self._snapshots = snapshots
        self._deps_factory = deps_factory
        self._notifier = notifier
        self._config = config
        self._code_commit = code_commit
        self._scrubber = scrubber
        self._threads: list[Any] = []
        self._lock = threading.Lock()

    # --- starting -----------------------------------------------------------------------

    def start_check(self, user: str, name: str, email: str) -> Any:
        job = self._store.start_job("check", user, name, email, self._code_commit, self._config.check_timeout_s)
        self._spawn(job)
        return job

    def start_publish(self, snapshot_id: str, user: str, name: str, email: str) -> Any:
        from publisher import steps

        starter = SimpleNamespace(store=self._store, snapshots=self._snapshots, code_commit=self._code_commit,
                                  config=self._config)
        job = steps.start_publish(starter, snapshot_id, user, name, email)
        self._spawn(job)
        return job

    def _spawn(self, job: Any) -> None:
        worker = threading.Thread(target=self._work, args=(job,), name=f"job-{job.id}", daemon=True)
        with self._lock:
            self._threads = [t for t in self._threads if t.is_alive()] + [worker]
        worker.start()

    def wait_idle(self, timeout: float) -> bool:
        """Wait for every worker and notice thread; False when something is still running after ``timeout``."""
        end = time.monotonic() + timeout
        while True:
            with self._lock:
                alive = [t for t in self._threads if t.is_alive()]
            if not alive:
                return True
            remaining = end - time.monotonic()
            if remaining <= 0:
                return False
            alive[0].join(min(remaining, 0.2))

    # --- the worker ---------------------------------------------------------------------

    def _work(self, job: Any) -> None:
        from publisher import steps

        deps = None
        try:
            deps = self._deps_factory(job)
            (steps.run_check if job.kind == "check" else steps.run_publish)(job, deps)
        except Exception:  # noqa: BLE001 - the lock must never stay held by a crashed job
            log.exception("job %s stopped unexpectedly", job.id)
            self._finish_unexpected(job)
        finally:
            closer = getattr(getattr(deps, "log", None), "close", None)
            if closer:
                closer()
        self._announce(job.id)

    def _finish_unexpected(self, job: Any) -> None:
        row = self._store.get(job.id)
        if row is None or row.state != "running":
            return
        state = "publish-failed-partial" if row.kind == "publish" and row.passes_started >= 1 else "failed"
        self._store.finish(job.id, state, reason_code="unknown",
                           reason_text="The job stopped unexpectedly: contact the owner (the job log has the detail)",
                           notice_state="pending")

    def _announce(self, job_id: int) -> None:
        row = self._store.get(job_id)
        if row is None or row.notice_state != "pending":
            return
        thread = self._notifier.notify_async(job_id, log=self._job_log_line(row))
        with self._lock:
            self._threads.append(thread)

    def _job_log_line(self, job: Any) -> Callable[[str], None]:
        from publisher.scrub import JobLog

        def write(text: str) -> None:
            job_log = JobLog(self._config.data / job.log_path, self._scrubber)
            try:
                job_log.line(text)
            finally:
                job_log.close()

        return write
