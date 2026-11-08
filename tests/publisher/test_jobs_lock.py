"""The single-running-job lock (spec 006 T041; FR-009, data-model "Job": the ``one_running`` index).

Seam: ``publisher.jobs.JobStore`` and the SQLite file itself, with real threads.
"""
import sqlite3
import threading

import pytest

from fake_clock import FakeClock

THREADS = 8


@pytest.fixture()
def store(tmp_path):
    from publisher.jobs import JobStore

    return JobStore(tmp_path / "publisher.sqlite3", site="rosart", clock=FakeClock())


def race(store, kind="check"):
    from publisher.jobs import JobAlreadyRunning

    barrier = threading.Barrier(THREADS)
    results = []

    def attempt(n):
        barrier.wait()
        try:
            results.append(store.start_job(kind, person_user=f"user{n}", person_name=f"User {n}", person_email="",
                                           code_commit="abc", timeout_s=60))
        except JobAlreadyRunning as exc:
            results.append(exc)
        except BaseException as exc:  # noqa: BLE001 - any other failure is a bug the test must show
            results.append(exc)

    threads = [threading.Thread(target=attempt, args=(n,)) for n in range(THREADS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    return results


def test_two_threads_starting_together_give_exactly_one_running_row(store, tmp_path):
    from publisher.jobs import JobAlreadyRunning

    for _ in range(10):
        results = race(store)

        winners = [r for r in results if not isinstance(r, BaseException)]
        losers = [r for r in results if isinstance(r, JobAlreadyRunning)]
        assert len(winners) == 1 and len(losers) == THREADS - 1, results
        assert all(loser.running.id == winners[0].id for loser in losers)
        with sqlite3.connect(tmp_path / "publisher.sqlite3") as db:
            assert db.execute("SELECT COUNT(*) FROM jobs WHERE state = 'running'").fetchone()[0] == 1
        store.finish(winners[0].id, "passed")


def test_a_direct_insert_of_a_second_running_row_raises(store, tmp_path):
    first = store.start_job("check", person_user="denise", person_name="", person_email="", code_commit="abc",
                            timeout_s=60)

    with sqlite3.connect(tmp_path / "publisher.sqlite3") as db:
        with pytest.raises(sqlite3.IntegrityError):
            db.execute("INSERT INTO jobs (site, kind, state, person_user, code_commit, created_at) "
                       "VALUES ('rosart', 'check', 'running', 'sam', 'abc', 'now')")
    assert store.running().id == first.id


def test_the_lock_is_per_site(store, tmp_path):
    store.start_job("check", person_user="denise", person_name="", person_email="", code_commit="abc", timeout_s=60)

    with sqlite3.connect(tmp_path / "publisher.sqlite3") as db:
        db.execute("INSERT INTO jobs (site, kind, state, person_user, code_commit, created_at) "
                   "VALUES ('other', 'check', 'running', 'sam', 'abc', 'now')")


def test_the_lock_is_released_by_the_final_state_write_alone(store):
    first = store.start_job("check", person_user="denise", person_name="", person_email="", code_commit="abc",
                            timeout_s=60)
    assert store.running() is not None

    store.finish(first.id, "failed", reason_code="x")

    assert store.running() is None
    store.start_job("check", person_user="sam", person_name="", person_email="", code_commit="abc", timeout_s=60)
