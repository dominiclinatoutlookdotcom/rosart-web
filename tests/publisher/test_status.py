"""The portal tile status: ``GET /status.json`` (spec 006 T079; research R-17, C5, data-model "Portal tile status",
hand-off H-8 service half; contracts/http-routes.md).

Seam: the real server (the ``web`` fixture, under ``/rosart`` and ``/other``), jobs written through the job store.
"""
from pathlib import Path

import pytest

PLANTED_REASON = "planted reason: the secret room is behind the bookshelf"


def finished_publish(web, state, **values):
    job = web.store.start_job("publish", "denise", "Denise Hill", "denise@example.org", "abc1234", 60)
    web.store.finish(job.id, state, reason_text=PLANTED_REASON, reason_code="unknown", **values)
    return job


def status(web):
    return web.get("/status.json", user=None)


def test_no_publish_yet_gives_null_and_no_attention_needed(web):
    reply = status(web)

    assert reply.status == 200 and reply.headers["content-type"] == "application/json"
    assert reply.json() == {"site": "rosart", "needs_human": False, "last_publish": None}
    assert reply.headers["cache-control"] == "no-store"


@pytest.mark.parametrize("state, result, needs_human", [
    ("published", "ok", False),
    ("published-cache-pending", "ok", True),
    ("failed", "failed", True),
    ("publish-failed-partial", "failed", True),
    ("interrupted", "failed", True),
])
def test_each_final_publish_state_gives_its_result_and_needs_human(web, state, result, needs_human):
    finished_publish(web, state)

    assert status(web).json() == {"site": "rosart", "needs_human": needs_human,
                                  "last_publish": {"date": "2026-11-08", "result": result}}


def test_the_keys_are_exactly_site_needs_human_and_last_publish_with_no_identity_needed(web):
    finished_publish(web, "published")

    body = status(web).json()

    assert set(body) == {"site", "needs_human", "last_publish"} and set(body["last_publish"]) == {"date", "result"}
    assert web.get("/status.json", user="denise").status == 200, "identity headers are ignored, not required"
    assert web.raw("GET", web.base + "/status.json", {"Remote-User": "xÿ"}).status == 200


def test_the_date_is_the_finish_date_in_melbourne_time(web):
    web.clock.step_wall(5 * 3600)          # 14:00 UTC is 01:00 the next day in Melbourne (UTC+11)
    finished_publish(web, "published")

    assert status(web).json()["last_publish"]["date"] == "2026-11-09"


def test_the_newest_finished_publish_decides_not_a_running_job_or_a_later_failed_check(web):
    finished_publish(web, "published")
    web.clock.advance(60)
    running = web.store.start_job("check", "john", "John Smith", "john@example.org", "abc1234", 60)
    before = status(web).json()
    web.store.finish(running.id, "failed", reason_code="email-rule", reason_text=PLANTED_REASON)

    after = status(web).json()

    assert before == after == {"site": "rosart", "needs_human": False,
                               "last_publish": {"date": "2026-11-08", "result": "ok"}}


def test_a_later_good_publish_clears_needs_human(web):
    finished_publish(web, "publish-failed-partial")
    assert status(web).json()["needs_human"] is True
    web.clock.advance(60)

    finished_publish(web, "published")

    assert status(web).json()["needs_human"] is False


def test_no_person_name_reason_or_number_ever_appears(web):
    finished_publish(web, "publish-failed-partial")

    body = status(web).body

    for private in ("denise", "Denise", "example.org", "planted reason", "bookshelf", "abc1234", "partial", '"1"'):
        assert private not in body


def test_the_status_is_503_with_the_attention_shape_when_the_database_cannot_be_opened(web):
    db = web.store.db_path
    for suffix in ("", "-wal", "-shm"):
        Path(str(db) + suffix).unlink(missing_ok=True)
    db.mkdir()

    reply = status(web)

    assert reply.status == 503
    assert reply.json() == {"site": "rosart", "needs_human": True, "last_publish": None}


def test_the_status_is_get_and_head_only(web):
    assert web.get("/status.json", method="HEAD", user=None).raw == b""
    assert web.get("/status.json", method="PUT", user=None).status == 405
    assert web.post("/status.json", user=None).status in (403, 405)
