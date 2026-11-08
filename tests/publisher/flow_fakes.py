"""Shared helpers for the check/publish flow tests (spec 006 Phase 4B): plain children, real git in temp folders.

``PlainChildren`` stands in for the job's ``ChildRunner`` (same ``run`` and ``mono`` seam) where a test does not
exercise process supervision. ``make_bare_repo`` builds a real bare repository with one commit.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Mapping, Optional, Sequence

from publisher.runner import Completed

GIT_ENV = {"GIT_AUTHOR_NAME": "Fixture", "GIT_AUTHOR_EMAIL": "fixture@example.org",
           "GIT_COMMITTER_NAME": "Fixture", "GIT_COMMITTER_EMAIL": "fixture@example.org"}


class PlainChildren:
    """``ChildRunner.run`` without supervision: runs the command and records it."""

    def __init__(self, mono=lambda: 0.0) -> None:
        self.calls: list[tuple[list[str], Optional[Path]]] = []
        self._mono = mono

    def mono(self) -> float:
        return self._mono()

    def run(self, argv: Sequence[str], env: Mapping[str, str], cwd: Optional[Path],
            timeout_mono: Optional[float]) -> Completed:
        self.calls.append((list(argv), cwd))
        done = subprocess.run(list(argv), env={**os.environ, **env}, cwd=cwd, capture_output=True,
                              stdin=subprocess.DEVNULL)
        return Completed(done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace"))


def git(*args: str, cwd: Optional[Path] = None, env: Optional[Mapping[str, str]] = None) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, env={**os.environ, **GIT_ENV, **(env or {})},
                          capture_output=True, stdin=subprocess.DEVNULL)
    if done.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {done.stderr.decode('utf-8', 'replace')}")
    return done.stdout.decode("utf-8", "replace")


def write_tree(root: Path, files: Mapping[str, "str | bytes"]) -> None:
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))


def make_bare_repo(path: Path, files: Mapping[str, "str | bytes"]) -> Path:
    """A bare repository at ``path`` whose only commit holds ``files``."""
    source = path.parent / (path.name + "-src")
    source.mkdir(parents=True)
    git("init", "-q", "-b", "main", cwd=source)
    write_tree(source, files)
    git("add", "-A", cwd=source)
    git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "seed", cwd=source)
    git("clone", "-q", "--bare", str(source), str(path))
    return path


def empty_bare_repo(path: Path) -> Path:
    git("init", "-q", "--bare", "-b", "main", str(path))
    return path


def head_files(bare: Path, prefix: str = "") -> dict[str, bytes]:
    """``{path: bytes}`` of the files in the bare repository's HEAD under ``prefix``."""
    names = git("--git-dir", str(bare), "ls-tree", "-r", "--name-only", "HEAD", "--", prefix or ".").splitlines()
    out = {}
    for name in names:
        done = subprocess.run(["git", "--git-dir", str(bare), "show", f"HEAD:{name}"], capture_output=True)
        out[name] = done.stdout
    return out


def tree_files(root: Path, prefix: str = "") -> dict[str, bytes]:
    base = root / prefix if prefix else root
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in sorted(base.rglob("*")) if p.is_file()}
