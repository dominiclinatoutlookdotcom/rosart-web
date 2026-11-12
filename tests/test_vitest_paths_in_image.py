"""The publish Check runs ``npm test`` in a work copy of the image's /app (Dockerfile), not in the repo.

A vitest file that imports or ``vi.mock``s a path outside what the image copies passes on a developer PC and
fails on the box (box job 10, 2026-11-12: ``tests/fixtures/export-snapshot/load.ts``). This test resolves every
relative import in the files vitest runs, and everything they import, and requires each target to be inside the
paths the Dockerfile's `build` and `final` stages copy.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE_SUFFIXES = (".ts", ".mts", ".js", ".mjs", ".json")
RESOLVE_SUFFIXES = ("", ".ts", ".mts", ".js", ".mjs", ".json", "/index.ts", "/index.js")
# import x from './a', import './a', export ... from './a', import('./a'), vi.mock('./a'), vi.importActual('./a')
SPEC = re.compile(
    r"""(?:\bfrom\s+|\bimport\s*\(\s*|\bimport\s+|\bvi\.(?:mock|doMock|importActual|importMock)\s*\(\s*)(['"])(\.{1,2}/[^'"]*)\1"""
)


def image_paths() -> list[Path]:
    """Sources of every COPY from the build context in the stages that make the service image."""
    paths: list[Path] = []
    stage = ""
    for line in (ROOT / "Dockerfile").read_text(encoding="utf-8").splitlines():
        if line.startswith("FROM "):
            stage = line.split(" AS ")[-1].strip() if " AS " in line else ""
            continue
        if stage not in ("build", "final") or not line.startswith("COPY ") or "--from" in line:
            continue
        args = [a for a in line.split()[1:] if not a.startswith("--")]
        paths += [ROOT / a for a in args[:-1]]
    return paths


def inside(path: Path, allowed: list[Path]) -> bool:
    return any(path == a or a in path.parents for a in allowed)


def resolve(base: Path, spec: str) -> Path | None:
    target = (base.parent / spec).resolve()
    for suffix in RESOLVE_SUFFIXES:
        candidate = Path(str(target) + suffix)
        if candidate.is_file():
            return candidate
    return None


def vitest_files() -> list[Path]:
    return sorted(p for folder in ("src", "scripts") for p in (ROOT / folder).rglob("*.test.ts"))


def test_the_image_copies_the_folders_vitest_runs_from():
    allowed = image_paths()
    assert ROOT / "src" in allowed and ROOT / "scripts" in allowed and ROOT / "vitest.config.ts" in allowed


def test_every_path_a_vitest_file_reaches_is_in_the_image():
    allowed = image_paths()
    problems: list[str] = []
    seen: set[Path] = set()
    queue = vitest_files()
    while queue:
        file = queue.pop()
        if file in seen:
            continue
        seen.add(file)
        if file.suffix == ".json":
            continue
        for match in SPEC.finditer(file.read_text(encoding="utf-8")):
            spec = match.group(2)
            target = resolve(file, spec)
            where = f"{file.relative_to(ROOT).as_posix()} -> {spec}"
            if target is None:
                problems.append(f"{where}: does not resolve to a file")
            elif not inside(target, allowed):
                problems.append(f"{where}: {target.relative_to(ROOT).as_posix()} is not copied into the image (Dockerfile)")
            else:
                queue.append(target)
    assert problems == []
