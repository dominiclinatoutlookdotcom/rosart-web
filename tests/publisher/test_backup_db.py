"""``python -m publisher backup-db``: a consistent copy of the job database (spec 006 T083; A-12, research R-13).

Seam: the CLI command (``publisher.__main__.main(["backup-db"])`` reading ``PUBLISHER_DATA``) over a real SQLite
database in WAL mode, with a writer thread active while the copy is made.
"""
import sqlite3
import threading
from contextlib import closing

import pytest

from fake_clock import FakeClock
from publisher.jobs import JobStore


@pytest.fixture()
def data(tmp_path, monkeypatch):
    folder = tmp_path / "data"
    monkeypatch.setenv("PUBLISHER_DATA", str(folder))
    return folder


def make_store(data):
    return JobStore(data / "publisher.sqlite3", site="rosart", clock=FakeClock())


def add_job(store, number):
    job = store.start_job("check", "maria", f"Maria {number}", "m@example.org", "abc1234", 60)
    store.finish(job.id, "failed", reason_code="unknown", reason_text="x", failures_json="[]")
    return job.id


def test_backup_db_writes_a_copy_that_passes_the_integrity_check_and_holds_every_job(data):
    from publisher.__main__ import main

    store = make_store(data)
    ids = [add_job(store, n) for n in range(5)]

    assert main(["backup-db"]) == 0

    copy = data / "backup" / "publisher.sqlite3"
    with closing(sqlite3.connect(copy)) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert [r[0] for r in db.execute("SELECT id FROM jobs ORDER BY id")] == ids
    assert not list((data / "backup").glob("*.tmp")), "no half-written file is left behind"


def test_backup_db_is_consistent_while_a_writer_is_active(data):
    from publisher.__main__ import main

    store = make_store(data)
    before = [add_job(store, n) for n in range(10)]
    stop = threading.Event()
    written = []

    def writer():
        number = 100
        while not stop.is_set():
            written.append(add_job(store, number))
            number += 1

    thread = threading.Thread(target=writer, daemon=True)
    thread.start()
    try:
        for _ in range(5):
            assert main(["backup-db"]) == 0
    finally:
        stop.set()
        thread.join(30)

    with closing(sqlite3.connect(data / "backup" / "publisher.sqlite3")) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        ids = [r[0] for r in db.execute("SELECT id FROM jobs ORDER BY id")]
        assert db.execute("SELECT COUNT(*) FROM jobs WHERE state = 'running'").fetchone()[0] <= 1
    assert ids[:len(before)] == before and ids == sorted(set(ids)) and set(ids) <= set(before + written)
    assert written, "the writer really was active"


def test_a_new_backup_replaces_the_old_one(data):
    from publisher.__main__ import main

    store = make_store(data)
    add_job(store, 1)
    main(["backup-db"])
    second = add_job(store, 2)

    main(["backup-db"])

    with closing(sqlite3.connect(data / "backup" / "publisher.sqlite3")) as db:
        assert second in [r[0] for r in db.execute("SELECT id FROM jobs")]


def test_backup_db_fails_plainly_when_there_is_no_database_and_keeps_the_last_good_copy(data, capsys):
    from publisher.__main__ import main

    store = make_store(data)
    add_job(store, 1)
    assert main(["backup-db"]) == 0
    good = (data / "backup" / "publisher.sqlite3").read_bytes()
    (data / "publisher.sqlite3").write_bytes(b"this is not a database")

    assert main(["backup-db"]) == 1

    assert (data / "backup" / "publisher.sqlite3").read_bytes() == good
    err = capsys.readouterr().err
    assert "backup-db" in err and "Traceback" not in err
