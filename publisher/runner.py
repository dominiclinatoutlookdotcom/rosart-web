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

from publisher.snapshots import canonical_json

log = logging.getLogger("rosart.runner")

SIGTERM = 15
SIGKILL = 9
POLL_S = 0.05
POSIX = os.name == "posix"


class FaultInjected(BaseException):
    """Raised by the test-only fault hook; a BaseException so it escapes every ``except Exception`` of a worker."""


class ServiceStopping(Exception):
    """The service received SIGTERM and starts no more jobs."""


class WorkerStopped(BaseException):
    """The service is stopping and a child of this worker was killed for it: the worker ends without writing a final
    state, because ``JobRunner.shutdown`` writes ``interrupted`` (a BaseException so no ``except Exception`` eats it)."""


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


def parse_proc_stat(stat_text: str) -> tuple[int, int, str]:
    """``(pid, pgid, state)`` from a ``/proc/<pid>/stat`` line (state is ``Z`` for a zombie)."""
    after_name = stat_text[stat_text.rindex(")") + 2:].split()
    return int(stat_text.split(" ", 1)[0]), int(after_name[2]), after_name[0]


def read_proc_table() -> Optional[list[tuple[int, int, str]]]:
    """``(pid, pgid, state)`` of every process, or None when there is no /proc (Windows, macOS)."""
    try:
        names = os.listdir("/proc")
    except OSError:
        return None
    table = []
    for name in names:
        if not name.isdigit():
            continue
        try:
            table.append(parse_proc_stat(Path(f"/proc/{name}/stat").read_text(encoding="ascii", errors="replace")))
        except (OSError, ValueError, IndexError):
            continue                    # the process ended while the table was read
    return table


class PosixOps:
    """The operating-system calls ``kill_and_wait`` makes; tests substitute fakes."""

    def killpg(self, pgid: int, sig: int) -> None:
        os.killpg(pgid, sig)

    def start_time(self, pid: int) -> Optional[int]:
        return proc_start_time(pid)


def kill_and_wait(pgid: int, started: Optional[int] = None, grace_s: float = 5.0, kill_wait_s: float = 5.0,
                  ops: Optional[Any] = None, sleep: Callable[[float], None] = time.sleep,
                  mono: Callable[[], float] = time.monotonic, reap: Optional[Callable[[], Any]] = None,
                  proc_table: Callable[[], Optional[Sequence[tuple[int, int, str]]]] = read_proc_table) -> None:
    """SIGTERM the group, wait up to ``grace_s``, SIGKILL, and return only when ``killpg(pgid, 0)`` fails.

    ``started`` is the leader's recorded start time: a leader that exists with another start time is a
    reused PID and nothing is signalled. ``reap`` is called while waiting (``Popen.poll``) so a
    zombie leader of our own child does not keep the group alive. A group whose remaining members are all
    zombies is gone too: with no init in the container (``docker run`` without ``--init``) nobody reaps an
    orphaned grandchild, and ``killpg(pgid, 0)`` succeeds while a zombie is left. ``proc_table`` reads
    ``(pid, pgid, state)`` of every process (None when there is no /proc).
    """
    ops = ops or PosixOps()
    if reap is None:
        def reap() -> None:
            """A leader that is our own child and already dead stays a zombie, and a zombie keeps its group
            "alive" for ``killpg(pgid, 0)``; collect it (a pid that is not our child raises and is ignored)."""
            try:
                os.waitpid(pgid, os.WNOHANG)
            except (ChildProcessError, OSError, AttributeError):
                pass
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
        table = proc_table()
        if table is None:
            return False
        return all(state == "Z" for _pid, group, state in table if group == pgid)

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


def fault_hook_from_env(environ: Mapping[str, str]) -> Optional[Callable[[Sequence[str]], None]]:
    """The test-only fault hook, enabled only by ``PUBLISHER_FAULT_HOOK`` (``selftest`` refuses it set, T100).

    ``build`` raises :class:`FaultInjected` in the worker just after the ``npm run build`` child has started and been
    recorded, outside the runner's own clean-up; ``pass-N`` does the same as the Nth real sync pass
    starts. It simulates a worker thread dying with a child running, which only the watchdog can clean up."""
    spec = (environ.get("PUBLISHER_FAULT_HOOK") or "").strip()
    if not spec:
        return None
    passes = [0]

    def hook(argv: Sequence[str]) -> None:
        name, point = Path(argv[0]).name, None
        if name == "npm" and list(argv[1:3]) == ["run", "build"]:
            point = "build"
        elif name == "aws" and list(argv[1:3]) == ["s3", "sync"] and "--dryrun" not in argv:
            passes[0] += 1
            point = f"pass-{passes[0]}"
        if point == spec:
            raise FaultInjected(point)

    return hook


class NullJobStore:
    """A job store that remembers nothing: for commands that run children outside a job (``seed-export-repo``)."""

    def set_child(self, job_id: int, pgid: int, started: Optional[int]) -> None:
        return None

    def clear_child(self, job_id: int) -> None:
        return None


class ChildRunner:
    """Runs a job's children under supervision; one per running job."""

    def __init__(self, store: Any, job_id: int, clock: Any, scrubber: Optional[Any] = None,
                 ops: Optional[Any] = None, fault: Optional[Callable[[Sequence[str]], None]] = None,
                 stop: Optional[threading.Event] = None) -> None:
        self._fault = fault
        self._stop = stop
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
            if self._fault is not None:
                self._fault(argv)           # test only: dies here, outside the clean-up below
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
        if self._stop is not None and self._stop.is_set():
            raise WorkerStopped()
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


class Watchdog(threading.Thread):
    """Every ``interval_s`` seconds: a running job whose worker thread died is cleaned up (E2, plan "Job supervision")."""

    def __init__(self, runner: "JobRunner", interval_s: float = 5.0) -> None:
        super().__init__(name="watchdog", daemon=True)
        self._runner = runner
        self._interval = interval_s
        self._halt = threading.Event()

    def run(self) -> None:
        while not self._halt.wait(self._interval):
            try:
                self._runner.reap_dead_worker()
            except Exception:  # noqa: BLE001 - the watchdog itself must never die
                log.exception("watchdog check failed")

    def stop(self) -> None:
        self._halt.set()


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
                 code_commit: str, scrubber: Any, stop: Optional[threading.Event] = None) -> None:
        self._stop = stop or threading.Event()
        self._store = store
        self._snapshots = snapshots
        self._deps_factory = deps_factory
        self._notifier = notifier
        self._config = config
        self._code_commit = code_commit
        self._scrubber = scrubber
        self._threads: list[Any] = []
        self._workers: dict[int, threading.Thread] = {}
        self._announced: set[int] = set()
        self._lock = threading.RLock()

    @property
    def stopping(self) -> bool:
        return self._stop.is_set()

    # --- starting -----------------------------------------------------------------------

    def start_check(self, user: str, name: str, email: str) -> Any:
        with self._lock:                    # the watchdog never sees a started job before its worker exists
            if self.stopping:
                raise ServiceStopping()
            job = self._store.start_job("check", user, name, email, self._code_commit, self._config.check_timeout_s)
            self._spawn(job)
        return job

    def start_publish(self, snapshot_id: str, user: str, name: str, email: str) -> Any:
        from publisher import steps

        starter = SimpleNamespace(store=self._store, snapshots=self._snapshots, code_commit=self._code_commit,
                                  config=self._config)
        with self._lock:
            if self.stopping:
                raise ServiceStopping()
            job = steps.start_publish(starter, snapshot_id, user, name, email)
            self._spawn(job)
        return job

    def _spawn(self, job: Any) -> None:
        worker = threading.Thread(target=self._work, args=(job,), name=f"job-{job.id}", daemon=True)
        with self._lock:
            self._threads = [t for t in self._threads if t.is_alive()] + [worker]
            self._workers[job.id] = worker
        worker.start()

    # --- a dead worker and a stop (E2) ---------------------------------------------------

    def reap_dead_worker(self, ops: Optional[Any] = None) -> Optional[int]:
        """If the running job has no live worker thread, stop its child group and end it; returns its id or None.

        The state is ``failed`` (code ``worker-died``), or ``publish-failed-partial`` once a pass was started."""
        with self._lock:
            job = self._store.running()
            if job is None:
                return None
            worker = self._workers.get(job.id)
            if worker is not None and worker.is_alive():
                return None
        log.error("job %s is running but its worker thread is gone: cleaning up", job.id)
        if job.child_pgid is not None:
            kill_and_wait(job.child_pgid, job.child_started, ops=ops)
            self._store.clear_child(job.id)
        from publisher import translate      # translate imports this module: import late

        partial = job.kind == "publish" and job.passes_started >= 1
        failure = translate.for_code("worker-died")
        if partial:
            failure["text"] = ("The publish stopped unexpectedly part-way: the live site may be partly updated. "
                               "A new publish will mirror the site fully")
            failure["action"] = "publish-again"
        written = self._store.finish(job.id, "publish-failed-partial" if partial else "failed",
                                     reason_code="worker-died", reason_text=failure["text"],
                                     failures_json=canonical_json([failure]), notice_state="pending")
        if written:
            self._announce(job.id)
        return job.id if written else None

    def shutdown(self, ops: Optional[Any] = None) -> None:
        """SIGTERM: start no more jobs, stop and await the running job's child group, write ``interrupted``."""
        with self._lock:
            self._stop.set()
            job = self._store.running()
        if job is None:
            return
        if job.child_pgid is not None:
            kill_and_wait(job.child_pgid, job.child_started, ops=ops)
            self._store.clear_child(job.id)
        from publisher import translate

        failure = translate.interrupted_failure(job.kind)
        if self._store.finish(job.id, "interrupted", reason_code=failure["code"], reason_text=failure["text"],
                              failures_json=canonical_json([failure]), notice_state="pending"):
            self._announce(job.id)
            self.wait_idle(5)

    @property
    def notifier(self) -> Any:
        return self._notifier

    def log_line_for(self, job: Any) -> Callable[[str], None]:
        """A function that appends a scrubbed line to ``job``'s log (for notices posted outside a worker)."""
        return self._job_log_line(job)

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
        with self._lock:
            if job_id in self._announced:
                return
            self._announced.add(job_id)
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
