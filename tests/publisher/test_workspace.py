"""The persistent workspace and the clean build (spec 006 T053; plan check phases 1 and 6, E4).

Seam: ``publisher.workspace.Workspace`` (``ensure``, ``current``, ``cleanup``) and ``clean_build``, over real temp
folders and a real bare repository.
"""
import os
import shutil
from pathlib import Path

import pytest

from flow_fakes import PlainChildren, empty_bare_repo, head_files, make_bare_repo, tree_files, write_tree

COMMIT_A, COMMIT_B = "aaaaaaa1111111", "bbbbbbb2222222"
EXPORT_FILES = {"export/shopify/products.json": '[{"title": "Toner"}]', "export/theme-content.json": "{}",
                "export/images/a.jpg": b"\xff\xd8binary"}


class SimulatedKill(BaseException):
    """The process dies here: nothing after it runs, not even cleanup of ordinary exceptions."""


@pytest.fixture
def parts(tmp_path):
    image = tmp_path / "image"
    write_tree(image, {"package.json": "{}", "src/site.ts": "x", "export/stale-from-image.json": "old",
                       "node_modules/.bin/astro": "bin"})
    bare = make_bare_repo(tmp_path / "export.git", EXPORT_FILES)
    return {"image": image, "bare": bare, "work": tmp_path / "work", "tmp": tmp_path}


def workspace(parts, copy=None):
    from publisher.workspace import Workspace

    kwargs = {"copy_tree": copy} if copy else {}
    return Workspace(parts["work"], "rosart", parts["image"], parts["bare"], **kwargs)


def ensure(ws, commit, children=None):
    return ws.ensure(commit, children or PlainChildren(), {"PATH": os.environ["PATH"]})


def test_a_new_image_commit_builds_a_complete_workspace_and_points_the_symlink_at_it(parts):
    ws = workspace(parts)

    path = ensure(ws, COMMIT_A)

    assert path == parts["work"] / f"rosart@{COMMIT_A}"
    assert (path / ".copy-complete").read_text().strip() == COMMIT_A
    assert (path / "package.json").is_file() and (path / "node_modules/.bin/astro").is_file()
    assert ws.current() == path
    assert (parts["work"] / "rosart").is_symlink()
    assert not (parts["work"] / f"rosart@{COMMIT_A}.tmp").exists()


def test_the_marker_is_written_last_after_the_whole_copy(parts):
    seen = {}

    def copy(src, dst):
        shutil.copytree(src, dst, symlinks=True)
        seen["marker_during_copy"] = (Path(dst) / ".copy-complete").exists()
        seen["folder"] = Path(dst).name

    ensure(workspace(parts, copy), COMMIT_A)

    assert seen == {"marker_during_copy": False, "folder": f"rosart@{COMMIT_A}.tmp"}


def test_a_second_image_commit_swaps_the_symlink_and_deletes_the_previous_folder(parts):
    ws = workspace(parts)
    first = ensure(ws, COMMIT_A)

    second = ensure(ws, COMMIT_B)

    assert ws.current() == second and second.name == f"rosart@{COMMIT_B}"
    assert not first.exists()


def test_the_same_commit_again_reuses_the_workspace_without_copying(parts):
    copies = []

    def copy(src, dst):
        copies.append(dst)
        shutil.copytree(src, dst, symlinks=True)

    ws = workspace(parts, copy)
    first = ensure(ws, COMMIT_A)
    (first / "export" / "cached-image.jpg").write_bytes(b"cache")

    assert ensure(ws, COMMIT_A) == first
    assert len(copies) == 1 and (first / "export" / "cached-image.jpg").exists()


def test_a_copy_killed_half_way_leaves_the_old_workspace_in_use_and_start_up_removes_the_leftovers(parts):
    ws = workspace(parts)
    old = ensure(ws, COMMIT_A)

    def dies_half_way(src, dst):
        write_tree(Path(dst), {"package.json": "{}"})
        raise SimulatedKill()

    with pytest.raises(SimulatedKill):
        ensure(workspace(parts, dies_half_way), COMMIT_B)

    tmp_folder = parts["work"] / f"rosart@{COMMIT_B}.tmp"
    stray = parts["work"] / "rosart@ccccccc3333333"
    stray.mkdir()
    assert ws.current() == old and tmp_folder.exists()

    ws.cleanup()

    assert not tmp_folder.exists() and not stray.exists()
    assert ws.current() == old and (old / ".copy-complete").is_file()


def test_a_failure_that_is_an_ordinary_error_removes_its_own_temp_folder(parts):
    ws = workspace(parts)
    old = ensure(ws, COMMIT_A)

    def fails(src, dst):
        write_tree(Path(dst), {"package.json": "{}"})
        raise OSError("disk full")

    with pytest.raises(OSError):
        ensure(workspace(parts, fails), COMMIT_B)

    assert not (parts["work"] / f"rosart@{COMMIT_B}.tmp").exists()
    assert ws.current() == old


def test_the_new_workspace_export_equals_the_bare_repository_head_not_the_old_workspace(parts):
    ws = workspace(parts)
    old = ensure(ws, COMMIT_A)
    (old / "export/shopify/products.json.part").write_text("half-written")
    (old / "export/shopify/products.json").write_text("[]")

    new = ensure(ws, COMMIT_B)

    assert tree_files(new, "export") == head_files(parts["bare"], "export")
    assert not (new / "export/stale-from-image.json").exists()
    assert not (new / "export/shopify/products.json.part").exists()


def test_an_empty_export_repository_stops_the_build_and_leaves_the_old_workspace(parts):
    from publisher.workspace import WorkspaceError

    ws = workspace(parts)
    old = ensure(ws, COMMIT_A)
    parts["bare"] = empty_bare_repo(parts["tmp"] / "empty.git")

    with pytest.raises(WorkspaceError):
        ensure(workspace(parts), COMMIT_B)

    assert ws.current() == old and not (parts["work"] / f"rosart@{COMMIT_B}.tmp").exists()


def test_clean_build_removes_every_build_output_and_cache_but_nothing_else(parts):
    from publisher.workspace import clean_build

    ws = ensure(workspace(parts), COMMIT_A)
    for folder in ("dist", ".astro", "node_modules/.astro", "node_modules/.vite"):
        write_tree(ws, {f"{folder}/stale.html": "stale page"})

    clean_build(ws)

    for folder in ("dist", ".astro", "node_modules/.astro", "node_modules/.vite"):
        assert not (ws / folder).exists(), folder
    assert (ws / "node_modules/.bin/astro").is_file() and (ws / "src/site.ts").is_file()
