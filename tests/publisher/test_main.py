"""Start-up wiring of ``python -m publisher serve`` (spec 006 T051/T052; FR-015, FR-016, research R-8).

Seams: ``publisher.__main__.create_app`` (the whole start-up, no socket), ``configure_logging`` and
the ``serve`` command run as a process on a loopback port.
"""
import io
import json
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from web_harness import ENV

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def env_for(tmp_path, **extra):
    return dict(ENV, PUBLISHER_DATA=str(tmp_path / "data"), PUBLISHER_TMP=str(tmp_path / "tmp"),
                PUBLISHER_BIND="127.0.0.1:0", **extra)


def test_create_app_wires_config_store_scrubber_and_the_credentials_file(tmp_path):
    from publisher.__main__ import create_app

    env = env_for(tmp_path, PUBLISHER_CODE_COMMIT="cafe123")
    app = create_app(env)

    assert app.code_commit == "cafe123" and app.config.site == "rosart"
    assert (tmp_path / "data" / "publisher.sqlite3").exists()
    assert (tmp_path / "tmp" / "aws" / "credentials").exists()
    assert "ROSART_AWS_SECRET_ACCESS_KEY" not in env and "SLACK_BOT_TOKEN" not in env
    assert app.scrubber.scrub("x aws-secret-planted-value y xoxb-planted-slack") == "x [redacted] y [redacted]"


def test_the_code_commit_is_unknown_when_the_image_did_not_say(tmp_path):
    from publisher.__main__ import create_app

    assert create_app(env_for(tmp_path)).code_commit == "unknown"


def test_an_unusable_configuration_stops_start_up_naming_the_key_and_no_value(tmp_path):
    from publisher.__main__ import create_app
    from publisher.config import ConfigError

    env = env_for(tmp_path)
    del env["ROSART_AWS_SECRET_ACCESS_KEY"]
    with pytest.raises(ConfigError) as caught:
        create_app(env)

    assert "ROSART_AWS_SECRET_ACCESS_KEY" in str(caught.value) and "aws-secret-planted-value" not in str(caught.value)


def test_service_log_lines_and_tracebacks_are_scrubbed(tmp_path):
    from publisher.__main__ import configure_logging, create_app

    app = create_app(env_for(tmp_path))
    stream = io.StringIO()
    handler = configure_logging(app.scrubber, stream)
    logger = logging.getLogger("rosart.test_main")
    try:
        logger.warning("token xoxb-planted-slack in a message")
        try:
            raise RuntimeError("failed with aws-secret-planted-value")
        except RuntimeError:
            logger.exception("step failed")
    finally:
        logging.getLogger().removeHandler(handler)

    output = stream.getvalue()
    assert "xoxb-planted-slack" not in output and "aws-secret-planted-value" not in output
    assert "[redacted]" in output and "Traceback" in output


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_serve_runs_as_a_process_answers_healthz_and_stops_when_terminated(tmp_path):
    port = free_port()
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env.update(env_for(tmp_path, PUBLISHER_BIND=f"127.0.0.1:{port}", PUBLISHER_CODE_COMMIT="cafe123"))
    proc = subprocess.Popen([sys.executable, "-m", "publisher", "serve"], cwd=REPO_ROOT, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        body = None
        for _ in range(100):
            try:
                with opener.open(f"http://127.0.0.1:{port}/rosart/healthz", timeout=2) as reply:
                    body = json.loads(reply.read())
                break
            except OSError:
                time.sleep(0.1)
        assert body == {"ok": True, "site": "rosart", "code_commit": "cafe123", "job_running": False}
    finally:
        proc.terminate()
        try:
            _, err = proc.communicate(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
            _, err = proc.communicate()
    for planted in ("aws-secret-planted-value", "client-secret-planted", "xoxb-planted-slack"):
        assert planted not in err
