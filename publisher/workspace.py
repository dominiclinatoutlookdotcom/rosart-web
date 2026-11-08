"""The persistent workspace and the clean build (spec 006 T054; E4, plan check phases 1 and 6).

``work/<site>`` is a symlink to a complete, writable copy of the image's ``/app`` at one code commit
(``work/<site>@<commit>``). A new commit builds ``work/<site>@<commit>.tmp``: copy ``/app``, put in the
bare export repository's HEAD ``export/`` (the latest committed export with its images, never an old
workspace's, which a failed export may have left half-written), write ``.copy-complete`` last, rename,
swap the symlink atomically (``os.replace`` of a new symlink) and delete the previous folder. A copy that
dies half-way leaves the old workspace in use; ``cleanup`` (start-up) removes the ``.tmp`` folder and every
copy the symlink does not point to.

``clean_build`` empties the build outputs and caches so no earlier output can reach a snapshot.
"""
from __future__ import annotations

import logging
import os
import shutil
import tarfile
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

log = logging.getLogger("rosart.workspace")

MARKER = ".copy-complete"
BUILD_OUTPUTS = ("dist", ".astro", "node_modules/.astro", "node_modules/.vite")
# Top-level folders of the image that never go into a workspace: export/ comes from the export repository.
NOT_COPIED = ("export", "dist")


class WorkspaceError(RuntimeError):
    """The workspace could not be built; the previous one is untouched."""


def clean_build(workspace: Path) -> None:
    """Delete ``dist/`` and the Astro and Vite caches (a planted stale page never reaches a snapshot)."""
    for name in BUILD_OUTPUTS:
        shutil.rmtree(Path(workspace) / name, ignore_errors=True)


class Workspace:
    def __init__(self, work_root: Path, site: str, image_root: Path, export_repo: Path,
                 copy_tree: Optional[Callable[[Path, Path], Any]] = None) -> None:
        self.work_root = Path(work_root)
        self.site = site
        self.image_root = Path(image_root)
        self.export_repo = Path(export_repo)
        self._copy_tree = copy_tree or self._default_copy

    # --- reading ------------------------------------------------------------------------

    @property
    def link(self) -> Path:
        return self.work_root / self.site

    def current(self) -> Optional[Path]:
        """The workspace the symlink points at, or None when there is none (or it is not complete)."""
        try:
            target = self.work_root / os.readlink(self.link)
        except OSError:
            return None
        return target if (target / MARKER).is_file() else None

    def folder(self, commit: str) -> Path:
        return self.work_root / f"{self.site}@{commit}"

    # --- building -----------------------------------------------------------------------

    def ensure(self, commit: str, children: Any, env: Mapping[str, str],
               timeout_mono: Optional[float] = None) -> Path:
        """The complete workspace for ``commit``: reused when current, otherwise built and swapped in."""
        target = self.folder(commit)
        if self.current() == target:
            return target
        self.work_root.mkdir(parents=True, exist_ok=True)
        tmp = self.work_root / f"{self.site}@{commit}.tmp"
        shutil.rmtree(tmp, ignore_errors=True)
        previous = self.current()
        try:
            self._copy_tree(self.image_root, tmp)
            self._put_export(tmp, children, env, timeout_mono)
            (tmp / MARKER).write_text(commit + "\n", encoding="utf-8")
            shutil.rmtree(target, ignore_errors=True)     # an incomplete leftover of this very commit
            os.replace(tmp, target)
        except Exception:
            # An ordinary failure cleans up after itself; a process killed here cannot, and start-up cleanup does.
            shutil.rmtree(tmp, ignore_errors=True)
            raise
        self._point_at(target)
        if previous is not None and previous != target:
            shutil.rmtree(previous, ignore_errors=True)
        return target

    def _default_copy(self, src: Path, dst: Path) -> None:
        def ignore(folder: str, names: list[str]) -> list[str]:
            return [name for name in names if name in NOT_COPIED] if Path(folder) == Path(src) else []

        shutil.copytree(src, dst, symlinks=True, ignore=ignore)

    def _put_export(self, tmp: Path, children: Any, env: Mapping[str, str], timeout_mono: Optional[float]) -> None:
        """``export/`` of the bare repository's HEAD, replacing whatever the copy brought."""
        shutil.rmtree(tmp / "export", ignore_errors=True)
        tmp.mkdir(parents=True, exist_ok=True)
        archive = tmp / ".export.tar"
        done = children.run(["git", "--git-dir", str(self.export_repo), "archive", "--format=tar", "-o", str(archive),
                             "HEAD", "export"], env=env, cwd=None, timeout_mono=timeout_mono)
        if done.returncode != 0:
            archive.unlink(missing_ok=True)
            raise WorkspaceError("the export repository has no committed export/ to start from: ask the owner to "
                                 "run seed-export-repo (" + (done.stderr.strip()[-200:] or "git archive failed") + ")")
        try:
            with tarfile.open(archive) as tar:
                tar.extractall(tmp, filter="data")
        finally:
            archive.unlink(missing_ok=True)

    def _point_at(self, target: Path) -> None:
        new_link = self.work_root / f".{self.site}.link-new"
        if new_link.is_symlink() or new_link.exists():
            new_link.unlink()
        os.symlink(target.name, new_link, target_is_directory=True)
        if os.name == "nt" and self.link.is_symlink():
            self.link.unlink()        # Windows cannot replace a directory symlink; the service runs on Linux only
        os.replace(new_link, self.link)

    # --- start-up -----------------------------------------------------------------------

    def cleanup(self) -> None:
        """Remove every ``*.tmp`` copy and every ``<site>@*`` folder the symlink does not point to."""
        current = self.current()
        if not self.work_root.is_dir():
            return
        for entry in self.work_root.iterdir():
            if entry.name == f".{self.site}.link-new":
                entry.unlink()
                continue
            if entry.name.startswith(f"{self.site}@") and entry != current and entry.is_dir() and not entry.is_symlink():
                log.info("removing the workspace copy %s", entry.name)
                shutil.rmtree(entry, ignore_errors=True)
