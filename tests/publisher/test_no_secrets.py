"""No secret reaches the database, the logs, a page, Slack or the export history (spec 006 T081; FR-015, FR-016,
US4 scenario 3, constitution II).

Seam: the real server over the whole fake world (``flow_web.FlowWeb``). The planted values are the Shopify client
ID and secret, the Shopify access token the fake store issues, the AWS key pair and the Slack token. They are echoed
by the fake ``npm`` (success and failure), by an exception from the theme read and by the fake AWS error text, then
every place the service writes or shows is scanned.
"""
import sqlite3

import pytest

from flow_fakes import git
from flow_harness import FakeChildren
from flow_web import FlowWeb
from publisher.runner import Completed
from web_harness import ENV, VALID

MARIA = dict(user="maria", name="Maria Lopez", email="maria@example.org")
ACCESS_TOKEN = "shpat_fake_0001"          # the first token the fake Shopify issues
SECRETS = (ENV["ROSART_SHOPIFY_CLIENT_ID"], ENV["ROSART_SHOPIFY_CLIENT_SECRET"], ACCESS_TOKEN,
           ENV["ROSART_AWS_ACCESS_KEY_ID"], ENV["ROSART_AWS_SECRET_ACCESS_KEY"], ENV["SLACK_BOT_TOKEN"])
CHATTER = "debug dump: " + " ".join(SECRETS) + f" split {ACCESS_TOKEN[:6]}\n{ACCESS_TOKEN[6:]}\n"


class Chatty(FakeChildren):
    """A fake ``npm`` that echoes every secret on stdout and stderr, whether it succeeds or fails."""

    def run(self, argv, env, cwd, timeout_mono):
        done = super().run(argv, env, cwd, timeout_mono)
        if argv[0] != "npm":
            return done
        return Completed(done.returncode, done.stdout + CHATTER, done.stderr + CHATTER)


@pytest.fixture(params=["/rosart", "/other"], ids=["base-rosart", "base-other"])
def world(request, tmp_path):
    fw = FlowWeb(tmp_path, base_path=request.param, children=Chatty(),
                 aws={"error_text": "InvalidClientTokenId: " + CHATTER,
                      "fail": lambda command: False})
    yield fw
    fw.close()


def press(fw, path, fields=None):
    reply = fw.web.post(path, fields, csrf=VALID, **MARIA)
    assert reply.status == 303, reply.body
    fw.wait()
    return int(reply.headers["location"].rsplit("/", 1)[1])


def database_text(fw):
    """Every value of every table plus the raw database files."""
    chunks = []
    with fw.flow.store.connection() as db:
        db.row_factory = sqlite3.Row
        chunks.extend(db.iterdump())
    for path in fw.flow.config.data.glob("publisher.sqlite3*"):
        if path.is_file():
            chunks.append(path.read_bytes().decode("latin-1"))
    return "\n".join(chunks)


def log_text(fw):
    return "\n".join(path.read_text(encoding="utf-8", errors="replace")
                     for path in fw.flow.config.data.rglob("*.log") if path.is_file())


def page_text(fw, job_ids):
    web = fw.web
    paths = ["/", "/jobs", "/status.json", "/healthz"]
    for job_id in job_ids:
        paths += [f"/jobs/{job_id}", f"/jobs/{job_id}/log"]
    return "\n".join(web.get(path, **MARIA).body for path in paths)


def slack_text(fw):
    """Bodies, URLs and every header except Authorization, where the token belongs (contracts/slack-messages.md)."""
    chunks = []
    for request in fw.slack.requests:
        headers = {k: v for k, v in request.headers.items() if k != "authorization"}
        chunks.append(f"{request.url}\n{headers}\n{request.body}")
    return "\n".join(chunks)


def history_text(fw):
    return git("--git-dir", str(fw.flow.bare), "log", "-p", "--all")


def assert_clean(fw, job_ids):
    scanned = {"database": database_text(fw), "log files": log_text(fw), "pages": page_text(fw, job_ids),
               "slack": slack_text(fw), "export history": history_text(fw)}
    for where, text in scanned.items():
        for secret in SECRETS:
            assert secret not in text, f"{secret!r} leaked into the {where}"
    assert scanned["slack"], "the scan has something to look at"


def test_a_passed_check_and_a_publish_leak_nothing_though_the_tools_echo_every_secret(world):
    check = press(world, "/check")
    snapshot_id = world.flow.snapshots.current_publishable().id
    publish = press(world, "/publish", {"snapshot": snapshot_id})

    assert world.flow.store.get(publish).state == "published"
    assert "[redacted]" in log_text(world), "the echoed values are in the log, redacted"
    assert_clean(world, [check, publish])


def test_a_failed_check_with_secrets_in_the_tool_output_and_in_an_exception_leaks_nothing(world):
    world.flow.children.fail = {("npm", "run", "build"): (1, f"Error: build blew up with {CHATTER}")}
    first = press(world, "/check")
    world.flow.children.fail = {}

    def broken_theme(run, fetch_url, theme_id, dest, require_main=True):
        raise RuntimeError(f"theme read failed: {CHATTER}")

    world.flow.read_theme = broken_theme
    second = press(world, "/check")

    assert world.flow.store.get(first).state == "failed" and world.flow.store.get(second).state == "failed"
    assert_clean(world, [first, second])


def test_a_publish_that_fails_with_secrets_in_the_aws_error_text_leaks_nothing(world):
    world.flow.aws_kwargs["fail_passes"] = {3}
    check = press(world, "/check")
    snapshot_id = world.flow.snapshots.current_publishable().id
    publish = press(world, "/publish", {"snapshot": snapshot_id})

    assert world.flow.store.get(publish).state == "publish-failed-partial"
    assert_clean(world, [check, publish])
