"""Slack #system notices (spec 006 T065; contracts/slack-messages.md, FR-013).

Seam: ``publisher.slack.SlackNotifier`` over a real job store, a fake clock and ``fake_slack.FakeSlack``.
"""
import json
import threading

import pytest

from fake_clock import FakeClock
from fake_slack import FakeSlack
from web_harness import ENV

PREFIX = "[rosart-publisher] on fbsrv: "


class World:
    def __init__(self, tmp_path):
        from publisher.config import load_config
        from publisher.jobs import JobStore
        from publisher.scrub import Scrubber
        from publisher.slack import SlackNotifier

        self.clock = FakeClock()
        self.config = load_config(dict(ENV, PUBLISHER_DATA=str(tmp_path / "data")), tmp_dir=tmp_path / "tmp")
        self.store = JobStore(self.config.data / "publisher.sqlite3", "rosart", self.clock)
        self.slack = FakeSlack()
        self.lines = []
        scrubber = Scrubber()
        for secret in self.config.secrets():
            scrubber.register(secret)
        self.notifier = SlackNotifier(self.config, self.store, self.slack.opener, self.clock.sleep, scrubber)

    def job(self, kind, state, name="Denise Hill", **values):
        job = self.store.start_job(kind, "denise", name, "denise@example.org", "abc1234", 1200)
        notice = {} if state == "passed" else {"notice_state": "pending"}
        self.store.finish(job.id, state, **notice, **values)
        return job.id

    def notify(self, job_id):
        return self.notifier.notify(job_id, log=self.lines.append)


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


SUMMARY = json.dumps({"uploaded": 12, "deleted": 2, "changes": "2 products, 1 page changed"})


def test_each_message_of_the_contract_table(world):
    cases = [
        (("publish", "published", dict(invalidation_id="I2ABC", summary_json=SUMMARY)),
         "job {id} published by Denise Hill: 12 uploaded, 2 deleted, 2 products, 1 page changed; invalidation I2ABC"),
        (("publish", "published-cache-pending", dict(invalidation_id="I2ABC", summary_json=SUMMARY)),
         "job {id} published by Denise Hill (cache refresh still running, invalidation I2ABC): 12 uploaded, 2 deleted"),
        (("check", "failed", dict(reason_code="email-rule", reason_text="Page Contact has info@deniva.com.au")),
         "job {id} check failed for Denise Hill: Page Contact has [email address]"),
        (("publish", "failed", dict(reason_code="site-changed", reason_text="The live site changed since this check")),
         "job {id} publish by Denise Hill stopped before upload: The live site changed since this check"),
        (("publish", "publish-failed-partial", dict(reason_code="partial-sync", reason_text="pass 3 failed")),
         "job {id} publish by Denise Hill FAILED part-way: the live site may be partly updated; "
         "the next Publish mirrors it fully. pass 3 failed"),
        (("check", "interrupted", {}), "job {id} (check by Denise Hill) was interrupted by a restart"),
        (("publish", "interrupted", {}),
         "job {id} (publish by Denise Hill) was interrupted by a restart; the live site may be partly updated"),
    ]
    for (kind, state, values), expected in cases:
        job_id = world.job(kind, state, **values)
        assert world.notifier.message(world.store.get(job_id)) == PREFIX + expected.format(id=job_id), state


def test_a_failed_history_commit_is_appended_to_the_published_line(world):
    job_id = world.job("publish", "published", invalidation_id="I2ABC", summary_json=SUMMARY,
                       export_commit_error="git failed")

    assert world.notifier.message(world.store.get(job_id)).endswith("; history commit FAILED, owner to fix")


def test_a_passed_check_posts_nothing(world):
    job_id = world.job("check", "passed")

    assert world.notifier.message(world.store.get(job_id)) is None
    assert world.notify(job_id) == "not-required" and not world.slack.requests


def test_the_changes_and_the_reason_are_cut_at_200_characters(world):
    long_reason = "x" * 500
    failed = world.job("check", "failed", reason_code="unknown", reason_text=long_reason)
    published = world.job("publish", "published", invalidation_id="I2ABC",
                          summary_json=json.dumps({"uploaded": 1, "deleted": 0, "changes": "c" * 500}))

    assert world.notifier.message(world.store.get(failed)).endswith(": " + "x" * 200)
    assert "c" * 200 in world.notifier.message(world.store.get(published))
    assert "c" * 201 not in world.notifier.message(world.store.get(published))


def test_no_email_address_and_no_query_string_reaches_slack(world):
    job_id = world.job("check", "failed", reason_code="unknown",
                       reason_text="Mail denise@example.org about https://rosart.com.au/p/?token=abc&x=1 now")

    text = world.notifier.message(world.store.get(job_id))

    assert "@example.org" not in text and "token=" not in text and "?" not in text
    assert "https://rosart.com.au/p/" in text


def test_the_message_goes_to_chat_post_message_for_the_system_channel_with_the_token_only_in_the_header(world):
    job_id = world.job("publish", "published", invalidation_id="I2ABC", summary_json=SUMMARY)

    assert world.notify(job_id) == "posted"

    (request,) = world.slack.requests
    token = world.config.slack_bot_token.reveal()
    assert request.url == "https://slack.com/api/chat.postMessage"
    assert request.headers["authorization"] == f"Bearer {token}"
    assert request.json()["channel"] == "C0PLANTED" and request.json()["text"].startswith(PREFIX)
    assert token not in request.url and token not in request.body
    assert token not in " ".join(world.lines)
    assert request.timeout == 10


def test_notice_state_follows_the_post(world):
    job_id = world.job("publish", "published", invalidation_id="I2ABC", summary_json=SUMMARY)
    assert world.store.get(job_id).notice_state == "pending"

    world.notify(job_id)

    assert world.store.get(job_id).notice_state == "posted"
    assert any("posted" in line for line in world.lines)


def test_one_attempt_and_one_retry_after_five_seconds(world):
    job_id = world.job("check", "failed", reason_code="unknown", reason_text="boom")
    world.slack.http_error_next(500)

    assert world.notify(job_id) == "posted"

    assert len(world.slack.requests) == 2 and world.clock.sleeps == [5]


def test_two_failures_end_failed_and_stop_after_the_retry(world):
    job_id = world.job("check", "failed", reason_code="unknown", reason_text="boom")
    world.slack.invalid_auth_next(times=3)

    assert world.notify(job_id) == "failed"

    assert len(world.slack.requests) == 2
    assert world.store.get(job_id).notice_state == "failed"
    assert any("invalid_auth" in line for line in world.lines), "the log says why it could not be posted"


def test_a_timeout_and_a_refused_connection_also_end_failed_without_raising(world):
    timed_out = world.job("check", "failed", reason_code="unknown", reason_text="a")
    refused = world.job("check", "failed", reason_code="unknown", reason_text="b")
    world.slack.timeout_next(2)
    world.slack.refused_next(2)

    assert world.notify(timed_out) == "failed" and world.notify(refused) == "failed"


def test_a_notice_is_posted_once_even_when_asked_again(world):
    job_id = world.job("publish", "interrupted")

    world.notify(job_id)
    world.notify(job_id)

    assert len(world.slack.requests) == 1


def test_a_running_job_is_never_announced(world):
    job = world.store.start_job("check", "denise", "Denise", "d@example.org", "abc1234", 1200)

    assert world.notify(job.id) == "not-required" and not world.slack.requests


def test_the_post_runs_on_a_background_thread_after_the_final_state_is_written(world):
    job_id = world.job("check", "failed", reason_code="unknown", reason_text="boom")
    seen = {}
    real_open = world.slack.open

    def open_(request, data=None, timeout=None):
        seen["thread"] = threading.current_thread()
        seen["state_when_posting"] = world.store.get(job_id).state
        return real_open(request, data, timeout)

    world.slack.open = open_

    thread = world.notifier.notify_async(job_id, log=world.lines.append)
    thread.join(10)

    assert seen["thread"] is not threading.current_thread() and seen["state_when_posting"] == "failed"
    assert world.store.get(job_id).state == "failed" and world.store.get(job_id).notice_state == "posted"
