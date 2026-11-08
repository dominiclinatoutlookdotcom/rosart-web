"""``python -m publisher``: the service's commands (spec 006 T052).

``serve`` starts the page; ``healthcheck`` asks the running service whether it is healthy (the container
HEALTHCHECK and the box checks call it); ``seed-export-repo`` makes the export history's one seed commit.
``backup-db`` copies the job database for the nightly backup. Further commands (``selftest``) arrive with their tasks.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sqlite3
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable, MutableMapping, Optional, Sequence, TextIO

from export_shopify import DEFAULT_STORE
from shopify_admin import AdminApiRunner, token_source_from_config

from publisher.aws import ServiceAwsRunner, service_aws
from publisher.clock import RealClock
from publisher.config import ConfigError, load_config
from publisher.egress import default_opener, make_opener_fetch
from publisher.exportrepo import ExportRepo, ExportRepoError
from publisher.jobs import JobStore, backup_database
from publisher.recovery import recover
from publisher.runner import ChildRunner, ChildStuck, JobRunner, NullJobStore, Watchdog, fault_hook_from_env
from publisher.scrub import JobLog, Scrubber
from publisher.slack import SlackNotifier
from publisher.snapshots import SnapshotStore
from publisher.steps import Deps
from publisher.web import App, make_server
from publisher.workspace import Workspace

DEFAULT_HEALTHCHECK_URL = "http://127.0.0.1:8080/rosart/healthz"
LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

log = logging.getLogger("rosart.main")


class _ScrubbingFormatter(logging.Formatter):
    def __init__(self, scrubber: Scrubber) -> None:
        super().__init__(LOG_FORMAT)
        self._scrubber = scrubber

    def format(self, record: logging.LogRecord) -> str:
        return self._scrubber.scrub(super().format(record))


def configure_logging(scrubber: Scrubber, stream: Optional[TextIO] = None, level: int = logging.INFO) -> logging.Handler:
    """Send the service's log to ``stream`` (stderr) through the scrubber; returns the handler added to the root logger."""
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(_ScrubbingFormatter(scrubber))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(level)
    return handler


APP_ROOT = Path(__file__).resolve().parent.parent       # the image's /app: code, deploy.config.json, export/


def make_deps_factory(config: Any, store: JobStore, snapshots: SnapshotStore, clock: Any, scrubber: Scrubber,
                      code_commit: str, opener: Any, app_root: Path = APP_ROOT,
                      aws_executable: Optional[str] = None,
                      workspace: Optional[Workspace] = None,
                      fault: Optional[Callable[[Any], None]] = None,
                      stop: Optional[threading.Event] = None) -> Callable[[Any], Deps]:
    """The real collaborators of one job: its own supervised ``ChildRunner``, log, workspace and AWS runner."""
    workspace = workspace or Workspace(config.data / "work", config.site, app_root, config.export_repo)

    def deps_for(job: Any) -> Deps:
        children = ChildRunner(store, job.id, clock, scrubber, fault=fault, stop=stop)
        job_log = JobLog(config.data / job.log_path, scrubber)

        def make_shopify(deadline: float) -> tuple[Any, Any]:
            tokens = token_source_from_config(config.token_config(), shop=DEFAULT_STORE, opener=opener,
                                              clock=clock.mono)
            return tokens, AdminApiRunner(DEFAULT_STORE, tokens, opener, clock.sleep, deadline, clock=clock.mono)

        def make_aws(deploy: Any, temp_dir: Path, timeout_s: Optional[float]) -> Any:
            runner = ServiceAwsRunner(config.aws_dir, children, executable=aws_executable,
                                      timeout=int(timeout_s or 3600), env=lambda: config.child_env(aws=True))
            return service_aws(deploy, runner, temp_dir)

        return Deps(config=config, store=store, snapshots=snapshots, clock=clock, children=children, log=job_log,
                    scrubber=scrubber, workspace=workspace,
                    export_repo=ExportRepo(config.export_repo, children, config.child_env(), config.tmp_dir / "git",
                                           config.tz_display),
                    code_commit=code_commit, make_shopify=make_shopify, fetch=make_opener_fetch(opener),
                    make_aws=make_aws, sleep=clock.sleep)

    return deps_for


def create_app(environ: Optional[MutableMapping[str, str]] = None, clock: Optional[Any] = None) -> App:
    """The whole start-up short of binding a socket: settings, secrets, credentials file, store, scrubber, runner."""
    environ = os.environ if environ is None else environ
    code_commit = (environ.get("PUBLISHER_CODE_COMMIT") or "").strip() or "unknown"
    config = load_config(environ)                      # takes every secret out of the environment
    scrubber = Scrubber()
    for secret in config.secrets():
        scrubber.register(secret)
    config.write_aws_files()
    for folder in (config.data, config.tmp_dir / "home", config.tmp_dir / "npm"):
        folder.mkdir(parents=True, exist_ok=True)
    clock = clock or RealClock()
    store = JobStore(config.data / "publisher.sqlite3", site=config.site, clock=clock)
    snapshots = SnapshotStore(store, config.data, config.site, clock, config.snapshot_window_s)
    opener = default_opener(DEFAULT_STORE)
    workspace = Workspace(config.data / "work", config.site, APP_ROOT, config.export_repo)
    stop = threading.Event()
    runner = JobRunner(store, snapshots,
                       make_deps_factory(config, store, snapshots, clock, scrubber, code_commit, opener,
                                         workspace=workspace, fault=fault_hook_from_env(environ), stop=stop),
                       SlackNotifier(config, store, opener, clock.sleep, scrubber), config, code_commit, scrubber,
                       stop=stop)
    reader = ExportRepo(config.export_repo, ChildRunner(NullJobStore(), 0, clock, scrubber), config.child_env(),
                        config.tmp_dir / "git", config.tz_display)
    app = App(config, store, clock, code_commit=code_commit, scrubber=scrubber, snapshots=snapshots, runner=runner,
              export_repo=reader)
    app.workspace = workspace
    return app


def serve() -> int:
    try:
        app = create_app()
    except ConfigError as exc:
        print(f"rosart-publisher NOT started: {exc}", file=sys.stderr)
        return 1
    configure_logging(app.scrubber)
    return serve_app(app)


def serve_app(app: App, announce: Optional[Callable[[int], None]] = None) -> int:
    """Recover from a restart, start the watchdog, serve until SIGTERM, then stop the running job cleanly (E2).

    ``announce(port)`` is called once the server is listening (the process tests read the port from it)."""
    try:
        report = recover(store=app.store, snapshots=app.snapshots, workspace=app.workspace, config=app.config,
                         notifier=app.runner.notifier, log_for=app.runner.log_line_for)
    except ChildStuck as exc:
        print(f"rosart-publisher NOT started: {exc}", file=sys.stderr)
        return 1
    if report.interrupted:
        log.warning("jobs interrupted by the restart: %s", report.interrupted)
    server = make_server(app, app.config.bind)
    watchdog = Watchdog(app.runner)
    watchdog.start()

    def terminate() -> None:
        try:
            app.runner.shutdown()
        finally:
            server.shutdown()

    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=terminate, daemon=True).start())
    log.info("rosart publisher serving %s on %s (code %s)", app.config.base_path, app.config.bind, app.code_commit)
    if announce is not None:
        announce(server.server_address[1])
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        watchdog.stop()
        server.server_close()
    return 0


def healthcheck(url: str, timeout: float = 5.0) -> int:
    """0 when ``url`` answers 200 with ``"ok": true``; 1 otherwise. Loopback only: no proxy."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=timeout) as reply:
            body = json.loads(reply.read().decode("utf-8"))
            status = reply.status
    except urllib.error.HTTPError as exc:
        print(f"unhealthy: {url} answered {exc.code}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"unhealthy: {url} not reachable ({type(exc).__name__})", file=sys.stderr)
        return 1
    if status != 200 or not isinstance(body, dict) or body.get("ok") is not True:
        print(f"unhealthy: {url} did not report ok", file=sys.stderr)
        return 1
    return 0


def seed_export_repo(app_root: Path, environ: Optional[MutableMapping[str, str]] = None) -> int:
    """One commit in the (empty) export repository from the image's committed ``export/`` (A-8)."""
    environ = os.environ if environ is None else environ
    repo_path = Path((environ.get("PUBLISHER_EXPORT_REPO") or "").strip() or "/export.git")
    tmp_dir = Path((environ.get("PUBLISHER_TMP") or "").strip() or "/tmp")
    code_commit = (environ.get("PUBLISHER_CODE_COMMIT") or "").strip() or "unknown"
    env = {"PATH": environ.get("PATH") or "/usr/local/bin:/usr/bin:/bin", "HOME": str(tmp_dir / "home"),
           "LANG": environ.get("LANG") or "C.UTF-8"}
    (tmp_dir / "home").mkdir(parents=True, exist_ok=True)
    repo = ExportRepo(repo_path, ChildRunner(NullJobStore(), 0, RealClock()), env, tmp_dir)
    try:
        sha = repo.seed(app_root, code_commit)
    except ExportRepoError as exc:
        print(f"seed-export-repo: {exc}", file=sys.stderr)
        return 1
    print(f"seed-export-repo: seeded {repo_path} with commit {sha[:12]} from {app_root / 'export'}")
    return 0


def backup_db(environ: Optional[MutableMapping[str, str]] = None) -> int:
    """A consistent copy of the job database in ``<data>/backup/`` for the nightly backup (research R-13)."""
    environ = os.environ if environ is None else environ
    data = Path((environ.get("PUBLISHER_DATA") or "").strip() or "/data")
    source, dest = data / "publisher.sqlite3", data / "backup" / "publisher.sqlite3"
    try:
        if not source.is_file():
            raise FileNotFoundError(f"there is no job database at {source}")
        backup_database(source, dest)
    except (sqlite3.Error, OSError) as exc:
        print(f"backup-db: could not back up the job database ({type(exc).__name__}: {exc}); the last good copy "
              "is untouched", file=sys.stderr)
        return 1
    print(f"backup-db: wrote {dest}")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m publisher", description="The Rosart publisher service.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="start the publisher page")
    check = commands.add_parser("healthcheck", help="exit non-zero unless the running service is healthy")
    check.add_argument("--url", default=DEFAULT_HEALTHCHECK_URL)
    commands.add_parser("backup-db", help="copy the job database to <data>/backup/ for the nightly backup")
    seed = commands.add_parser("seed-export-repo", help="make the export history's first commit from the image's export/")
    seed.add_argument("--app-root", type=Path, default=Path(__file__).resolve().parent.parent,
                      help="the folder holding the committed export/ (default: the image's /app)")
    args = parser.parse_args(argv)
    if args.command == "serve":
        return serve()
    if args.command == "backup-db":
        return backup_db()
    if args.command == "seed-export-repo":
        return seed_export_repo(args.app_root)
    return healthcheck(args.url)


if __name__ == "__main__":
    sys.exit(main())
