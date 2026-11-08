"""Liveness: /healthz and ``python -m publisher healthcheck`` (spec 006 T051; FR-002, FR-021).

Seams: ``GET /healthz`` on the real server, ``publisher.__main__.main(["healthcheck", ...])`` and the
module run as a process.
"""
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_healthz_reports_ok_site_commit_and_no_running_job(web):
    reply = web.get("/healthz", user=None)

    assert reply.status == 200 and reply.headers["content-type"] == "application/json"
    assert reply.json() == {"ok": True, "site": "rosart", "code_commit": "deadbeef1234", "job_running": False}
    assert reply.headers["cache-control"] == "no-store"


def test_healthz_ignores_identity_headers_entirely(web):
    assert web.get("/healthz", user=None).status == 200
    assert web.get("/healthz", user="denise", groups="").status == 200
    assert web.raw("GET", web.base + "/healthz", {"Remote-User": "xÿ"}).status == 200


def test_healthz_says_when_a_job_is_running_and_names_nobody(web):
    web.store.start_job("publish", person_user="denise", person_name="Denise Hill", person_email="d@example.org",
                        code_commit="abc", timeout_s=60)

    reply = web.get("/healthz", user=None)

    assert reply.json()["job_running"] is True
    for private in ("denise", "Denise", "d@example.org", "client-secret-planted", "aws-secret-planted-value"):
        assert private not in reply.body


def test_healthz_is_503_with_ok_false_when_the_database_cannot_be_opened(web):
    db = web.store.db_path
    for suffix in ("", "-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    db.mkdir()                                      # a folder where the file should be

    reply = web.get("/healthz", user=None)

    assert reply.status == 503 and reply.json()["ok"] is False
    assert set(reply.json()) == {"ok", "site", "code_commit", "job_running"}


def test_healthz_is_head_and_get_only(web):
    assert web.get("/healthz", method="HEAD", user=None).raw == b""
    assert web.get("/healthz", method="PUT", user=None).status == 405


# --- python -m publisher healthcheck ------------------------------------------------------------

def healthcheck(*args):
    from publisher.__main__ import main

    return main(["healthcheck", *args])


def test_the_default_healthcheck_url_is_the_one_in_the_contract():
    from publisher.__main__ import DEFAULT_HEALTHCHECK_URL

    assert DEFAULT_HEALTHCHECK_URL == "http://127.0.0.1:8080/rosart/healthz"


def test_healthcheck_exits_zero_when_healthz_is_ok(web, capsys):
    assert healthcheck("--url", f"http://127.0.0.1:{web.port}{web.base}/healthz") == 0


def test_healthcheck_exits_non_zero_on_a_503(web):
    web.store.db_path.unlink()
    for suffix in ("-wal", "-shm"):
        Path(str(web.store.db_path) + suffix).unlink(missing_ok=True)
    web.store.db_path.mkdir()

    assert healthcheck("--url", f"http://127.0.0.1:{web.port}{web.base}/healthz") != 0


def test_healthcheck_exits_non_zero_when_the_page_says_not_ok_even_with_200(tmp_path, capsys):
    import threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class NotOk(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            body = json.dumps({"ok": False}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), NotOk)
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    try:
        assert healthcheck("--url", f"http://127.0.0.1:{server.server_address[1]}/rosart/healthz") != 0
    finally:
        server.shutdown()
        server.server_close()


def test_healthcheck_exits_non_zero_when_nothing_is_listening():
    assert healthcheck("--url", "http://127.0.0.1:1/rosart/healthz") != 0


def test_the_module_runs_as_a_process_and_its_healthcheck_fails_when_nothing_listens():
    done = subprocess.run([sys.executable, "-m", "publisher", "healthcheck", "--url", "http://127.0.0.1:1/rosart/healthz"],
                          cwd=REPO_ROOT, capture_output=True, text=True, timeout=60,
                          env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"})

    assert done.returncode != 0 and "Traceback" not in done.stderr


def test_the_module_without_a_command_prints_usage_and_fails():
    done = subprocess.run([sys.executable, "-m", "publisher"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=60)

    assert done.returncode == 2 and "usage" in (done.stderr + done.stdout).lower()
