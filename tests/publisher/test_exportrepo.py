"""The export repository: seed, summary against HEAD, commit from the snapshot, pending retry (spec 006 T059).

data-model "Export repository", research R-9, C3, E5, FR-012, SC-007. Seam: ``publisher.exportrepo.ExportRepo``
over a real temporary bare repository and real folders.
"""
import json
import shutil
from datetime import datetime, timezone

import pytest

from fake_clock import FakeClock
from flow_fakes import PlainChildren, empty_bare_repo, git, head_files, make_bare_repo, tree_files, write_tree

PRODUCTS_OLD = json.dumps([{"id": "1", "handle": "toner", "title": "Old Toner"}])
PRODUCTS_NEW = json.dumps([{"id": "1", "handle": "toner", "title": "New Toner"}])
WHEN = datetime(2026, 11, 8, 14, 5, tzinfo=timezone.utc)      # 01:05 on 9 November in Melbourne
BASE_FILES = {"export/shopify/products.json": PRODUCTS_OLD, "export/theme-content.json": "{}",
              "export/images/a.jpg": b"\xff\xd8old"}


def repo_for(path, children=None, tmp=None):
    from publisher.exportrepo import ExportRepo

    return ExportRepo(path, children or PlainChildren(), {}, tmp, tz_display="Australia/Melbourne")


@pytest.fixture
def seeded(tmp_path):
    bare = make_bare_repo(tmp_path / "export.git", BASE_FILES)
    return repo_for(bare, tmp=tmp_path / "tmp"), bare


def snapshot_with(tmp_path, files, name="snap"):
    folder = tmp_path / name
    write_tree(folder, {"dist/index.html": "x", **files})
    return folder


def commit(repo, snapshot, **overrides):
    values = dict(person_name="Denise Hill", person_email="denise@example.org", when=WHEN, job_id=7,
                  snapshot_id="rosart-6-abcdefabcdef", code_commit="deadbeef", invalidation_id="I2EXAMPLE")
    values.update(overrides)
    return repo.commit(snapshot, repo.summary(snapshot), **values)


# --- seed ------------------------------------------------------------------------------------------

def test_seed_makes_one_commit_from_the_images_export_without_the_theme_code(tmp_path):
    bare = empty_bare_repo(tmp_path / "export.git")
    image = tmp_path / "image"
    write_tree(image, {**BASE_FILES, "export/theme/layout/theme.liquid": "licensed", "src/x.ts": "not export"})
    repo = repo_for(bare, tmp=tmp_path / "tmp")

    sha = repo.seed(image, code_commit="deadbeef")

    assert git("--git-dir", str(bare), "rev-list", "--count", "HEAD").strip() == "1"
    assert git("--git-dir", str(bare), "rev-parse", "HEAD").strip() == sha
    assert sorted(head_files(bare)) == sorted(BASE_FILES)


def test_seed_refuses_a_repository_that_already_has_history(seeded):
    from publisher.exportrepo import ExportRepoError

    repo, bare = seeded

    with pytest.raises(ExportRepoError, match="not empty"):
        repo.seed(bare.parent, code_commit="deadbeef")
    assert git("--git-dir", str(bare), "rev-list", "--count", "HEAD").strip() == "1"


# --- summary ---------------------------------------------------------------------------------------

def test_the_summary_compares_the_snapshot_export_against_head_with_a_private_index(seeded, tmp_path):
    repo, bare = seeded
    snapshot = snapshot_with(tmp_path, {**BASE_FILES, "export/shopify/products.json": PRODUCTS_NEW,
                                        "export/images/b.jpg": b"new"})

    changes = repo.summary(snapshot)

    assert changes.records["products"].changed == ["New Toner"]
    assert changes.images.added == 1
    assert not (bare / "index").exists(), "the bare repository's own index is never used"
    assert not (snapshot / ".git").exists()


def test_a_snapshot_equal_to_head_summarises_as_empty(seeded, tmp_path):
    repo, _ = seeded

    assert repo.summary(snapshot_with(tmp_path, BASE_FILES)).empty


# --- commit ----------------------------------------------------------------------------------------

def test_a_commit_holds_the_snapshot_export_not_the_workspace_and_leaves_out_theme_and_part_files(seeded, tmp_path):
    repo, bare = seeded
    snapshot = snapshot_with(tmp_path, {"export/shopify/products.json": PRODUCTS_NEW,
                                        "export/theme-content.json": '{"changed": true}',
                                        "export/theme/layout/theme.liquid": "licensed",
                                        "export/images/b.jpg.part": "half"})
    workspace = tmp_path / "workspace"
    write_tree(workspace, {"export/shopify/products.json": "[]", "export/decoy.json": "from the workspace"})

    commit(repo, snapshot)

    files = head_files(bare)
    assert sorted(files) == ["export/shopify/products.json", "export/theme-content.json"]
    assert files["export/shopify/products.json"] == PRODUCTS_NEW.encode()
    assert files["export/theme-content.json"] == b'{"changed": true}'


def test_the_commit_names_the_person_the_publisher_the_melbourne_date_the_summary_and_the_trailers(seeded, tmp_path):
    repo, bare = seeded
    snapshot = snapshot_with(tmp_path, {**BASE_FILES, "export/shopify/products.json": PRODUCTS_NEW})

    sha = commit(repo, snapshot)

    fields = git("--git-dir", str(bare), "log", "-1", "--format=%an|%ae|%cn|%ce|%s", sha).strip()
    assert fields == "Denise Hill|denise@example.org|rosart-publisher|rosart-publisher@fbsrv|Publish 2026-11-09 by Denise Hill"
    body = git("--git-dir", str(bare), "log", "-1", "--format=%B", sha)
    assert "Products: 1 changed, 0 added, 0 removed (New Toner)" in body
    trailers = {line.partition(": ")[0]: line.partition(": ")[2] for line in body.splitlines() if ": " in line}
    assert (trailers["Job"], trailers["Snapshot"], trailers["Code"], trailers["Invalidation"]) == (
        "7", "rosart-6-abcdefabcdef", "deadbeef", "I2EXAMPLE")
    assert git("--git-dir", str(bare), "rev-list", "--count", "HEAD").strip() == "2"


def test_a_publish_with_nothing_new_still_records_one_commit(seeded, tmp_path):
    repo, bare = seeded

    commit(repo, snapshot_with(tmp_path, BASE_FILES))

    assert git("--git-dir", str(bare), "rev-list", "--count", "HEAD").strip() == "2"


def test_the_repository_has_no_remote_and_the_wrapper_refuses_remote_commands(seeded):
    from publisher.exportrepo import ExportRepoError

    _, bare = seeded
    children = PlainChildren()
    repo = repo_for(bare, children=children)

    assert git("--git-dir", str(bare), "remote").strip() == ""
    for command in ("push", "remote", "fetch", "clone", "submodule"):
        with pytest.raises(ExportRepoError, match="not allowed"):
            repo.git(command, "origin")
    with pytest.raises(ExportRepoError):
        repo.git("-c", "x.y=z", "push")
    assert not children.calls, "a refused command never starts a child"


# --- pending commits -------------------------------------------------------------------------------

class World:
    """Jobs and snapshot folders for the retry cases."""

    def __init__(self, tmp_path, repo):
        from publisher.jobs import JobStore
        from publisher.snapshots import SnapshotStore

        self.clock = FakeClock(wall=WHEN)
        self.store = JobStore(tmp_path / "data" / "publisher.sqlite3", "rosart", self.clock)
        self.snaps = SnapshotStore(self.store, tmp_path / "data", "rosart", self.clock, 3600)
        self.repo = repo

    def pending_publish(self, title, error="git failed"):
        folder_name = f"rosart-0-{title.lower().replace(' ', '')[:12].ljust(12, '0')}"
        write_tree(self.snaps.folder(folder_name), {"dist/index.html": title, "export/shopify/products.json":
                   json.dumps([{"id": "1", "handle": "toner", "title": title}]), "export/theme-content.json": "{}"})
        job = self.store.start_job("publish", "denise", "Denise Hill", "denise@example.org", "deadbeef", 1200)
        self.store.finish(job.id, "published", snapshot_id=folder_name, invalidation_id="I1",
                          export_commit_error=error)
        return job, folder_name


def test_pending_commits_are_retried_oldest_first_one_commit_each_and_clear_the_error(seeded, tmp_path):
    repo, bare = seeded
    world = World(tmp_path, repo)
    first, _ = world.pending_publish("First Toner")
    second, _ = world.pending_publish("Second Toner")

    done = repo.retry_pending(world.store, world.snaps)

    assert done == [first.id, second.id]
    subjects = git("--git-dir", str(bare), "log", "--format=%s%n%b", "-n", "2", "--reverse")
    assert subjects.index("First Toner") < subjects.index("Second Toner")
    for job in (first, second):
        row = world.store.get(job.id)
        assert row.export_commit and row.export_commit_error is None
    assert repo.retry_pending(world.store, world.snaps) == []


def test_a_failed_retry_keeps_the_error_and_does_not_raise_and_the_next_one_waits(seeded, tmp_path):
    repo, bare = seeded
    world = World(tmp_path, repo)
    first, first_folder = world.pending_publish("First Toner")
    second, _ = world.pending_publish("Second Toner")
    moved = world.snaps.folder(first_folder).with_name("moved")
    world.snaps.folder(first_folder).rename(moved)

    assert repo.retry_pending(world.store, world.snaps) == []

    row = world.store.get(first.id)
    assert row.export_commit is None and row.export_commit_error
    assert world.store.get(second.id).export_commit is None, "history order is kept: the newer commit waits"
    moved.rename(world.snaps.folder(first_folder))
    assert repo.retry_pending(world.store, world.snaps) == [first.id, second.id]


def test_a_summary_made_while_a_commit_is_pending_is_labelled_with_the_last_recorded_publish(seeded, tmp_path):
    repo, _ = seeded
    world = World(tmp_path, repo)

    assert repo.summary_label(world.store) is None
    world.pending_publish("First Toner")

    assert repo.summary_label(world.store).startswith("since the last recorded publish ")


def test_the_label_names_the_date_of_the_head_commit_in_melbourne(seeded, tmp_path):
    repo, _ = seeded
    world = World(tmp_path, repo)
    snapshot = snapshot_with(tmp_path, {**BASE_FILES, "export/shopify/products.json": PRODUCTS_NEW})
    commit(repo, snapshot)                       # 9 November in Melbourne
    world.pending_publish("Later Toner")

    assert repo.summary_label(world.store) == "since the last recorded publish 9 November 2026"
