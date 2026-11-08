"""``python -m publisher``: the service's commands (spec 006 T052).

``serve`` starts the page; ``healthcheck`` asks the running service whether it is healthy (the container
HEALTHCHECK and the box checks call it). Further commands (``backup-db``, ``seed-export-repo``,
``selftest``) arrive with their tasks.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, MutableMapping, Optional, Sequence, TextIO

from publisher.clock import RealClock
from publisher.config import ConfigError, load_config
from publisher.jobs import JobStore
from publisher.scrub import Scrubber
from publisher.web import App, make_server

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


def create_app(environ: Optional[MutableMapping[str, str]] = None, clock: Optional[Any] = None) -> App:
    """The whole start-up short of binding a socket: settings, secrets, credentials file, store, scrubber."""
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
    return App(config, store, clock, code_commit=code_commit, scrubber=scrubber)


def serve() -> int:
    try:
        app = create_app()
    except ConfigError as exc:
        print(f"rosart-publisher NOT started: {exc}", file=sys.stderr)
        return 1
    configure_logging(app.scrubber)
    server = make_server(app, app.config.bind)
    signal.signal(signal.SIGTERM, lambda *_: threading.Thread(target=server.shutdown, daemon=True).start())
    log.info("rosart publisher serving %s on %s (code %s)", app.config.base_path, app.config.bind, app.code_commit)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
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


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m publisher", description="The Rosart publisher service.")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("serve", help="start the publisher page")
    check = commands.add_parser("healthcheck", help="exit non-zero unless the running service is healthy")
    check.add_argument("--url", default=DEFAULT_HEALTHCHECK_URL)
    args = parser.parse_args(argv)
    if args.command == "serve":
        return serve()
    return healthcheck(args.url)


if __name__ == "__main__":
    sys.exit(main())
