"""What changed in ``export/`` since the last commit, in plain words; and the publish commit.

``make changes`` prints the store content a publish would put live: products, pages,
collections, menus and blogs added, changed or removed (by title), theme editor content changed
(homepage, header, footer, ...), image counts, and the live URLs to spot-check afterwards.

``make publish-commit`` (``--commit``) commits the export after a successful publish: it stages
``export/`` only and writes ``Publish <YYYY-MM-DD> by <git user.name>`` with the counts in the body.
It refuses when anything outside ``export/`` is changed, and it never pushes: the owner does.

Git output is read as bytes and decoded as UTF-8 (titles are not ASCII), never as console text.

Usage::

    python tools/export_changes.py [--commit]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
EXPORT = "export"
# The site's canonical origin (siteUrl in src/site.config.ts).
SITE_URL = "https://rosart.com.au"

# kind -> file under export/shopify/
RECORD_FILES = {
    "products": "products.json",
    "pages": "pages.json",
    "collections": "collections.json",
    "menus": "menus.json",
    "blogs": "blogs.json",
}
THEME_FILE = f"{EXPORT}/theme-content.json"
IMAGES_DIR = f"{EXPORT}/images/"
MANIFEST = f"{IMAGES_DIR}manifest.json"
SUMMARISED = {f"{EXPORT}/shopify/{name}" for name in RECORD_FILES.values()} | {THEME_FILE, MANIFEST}


class ChangesError(RuntimeError):
    """A refusal; the message says what to do."""


@dataclass
class RecordChanges:
    added: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    urls: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.added) + len(self.changed) + len(self.removed)


@dataclass
class ImageChanges:
    added: int = 0
    changed: int = 0
    removed: int = 0

    @property
    def total(self) -> int:
        return self.added + self.changed + self.removed


@dataclass
class ExportChanges:
    records: dict[str, RecordChanges]
    theme: list[str]
    images: ImageChanges
    other_files: list[str]
    paths: list[str]  # every changed path under export/

    @property
    def empty(self) -> bool:
        return not self.paths


# --- git ------------------------------------------------------------------------------------

def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    completed = subprocess.run(["git", *args], cwd=repo, capture_output=True, stdin=subprocess.DEVNULL)
    if check and completed.returncode != 0:
        raise ChangesError(f"git {' '.join(args[:2])} failed: "
                           f"{completed.stderr.decode('utf-8', errors='replace').strip()[-400:]}")
    return completed


def status(repo: Path, *pathspec: str) -> list[tuple[str, str]]:
    """``(XY, path)`` for every changed or untracked file, from ``git status -z`` (no quoting)."""
    raw = _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *pathspec).stdout
    entries = raw.decode("utf-8").split("\0")
    result: list[tuple[str, str]] = []
    index = 0
    while index < len(entries):
        entry = entries[index]
        index += 1
        if len(entry) < 4:
            continue
        code, path = entry[:2], entry[3:]
        if "R" in code or "C" in code:
            index += 1  # the rename's original path follows
        result.append((code, path))
    return result


def _head_json(repo: Path, path: str) -> Any:
    completed = _git(repo, "show", f"HEAD:{path}", check=False)
    if completed.returncode != 0:
        return None
    return json.loads(completed.stdout.decode("utf-8"))


def _work_json(repo: Path, path: str) -> Any:
    file = repo / path
    if not file.is_file():
        return None
    return json.loads(file.read_text(encoding="utf-8"))


# --- the summary ----------------------------------------------------------------------------

def _key(record: dict) -> str:
    return str(record.get("id") or record.get("handle") or record.get("title"))


def _title(record: dict) -> str:
    return str(record.get("title") or record.get("handle") or record.get("id"))


def diff_records(old: Optional[list], new: Optional[list]) -> RecordChanges:
    before = {_key(r): r for r in old or [] if isinstance(r, dict)}
    after = {_key(r): r for r in new or [] if isinstance(r, dict)}
    changes = RecordChanges()
    for key, record in after.items():
        if key not in before:
            changes.added.append(_title(record))
        elif record != before[key]:
            changes.changed.append(_title(record))
    changes.removed = [_title(r) for key, r in before.items() if key not in after]
    return changes


def _product_urls(old: Optional[list], new: Optional[list]) -> list[str]:
    before = {_key(r): r for r in old or [] if isinstance(r, dict)}
    return [f"{SITE_URL}/products/{r['handle']}/" for r in new or []
            if isinstance(r, dict) and r.get("handle") and before.get(_key(r)) != r]


def theme_label(key: str) -> str:
    name = key.rsplit("/", 1)[-1].removesuffix(".json")
    if key == "global_settings":
        return "logo and global settings"
    if name == "index":
        return "homepage"
    if name in ("header-group", "footer-group"):
        return name.split("-")[0]
    if name.startswith("page."):
        return f"{name[len('page.'):].replace('-', ' ')} page"
    if name.startswith("product"):
        return "product page layout"
    if name.startswith("collection"):
        return "collection page layout"
    if name == "404":
        return "404 page"
    return key


def diff_theme(old: Optional[dict], new: Optional[dict]) -> list[str]:
    old, new = old or {}, new or {}

    def parts(doc: dict) -> dict[str, Any]:
        flat: dict[str, Any] = {"global_settings": doc.get("global_settings")}
        for group in ("section_groups", "templates"):
            flat.update(doc.get(group) or {})
        return flat

    before, after = parts(old), parts(new)
    return sorted({theme_label(k) for k in before.keys() | after.keys() if before.get(k) != after.get(k)})


def summarise(repo: Path) -> ExportChanges:
    changed = status(repo, EXPORT)
    paths = [path for _, path in changed]
    records: dict[str, RecordChanges] = {}
    for kind, name in RECORD_FILES.items():
        path = f"{EXPORT}/shopify/{name}"
        old, new = (_head_json(repo, path), _work_json(repo, path)) if path in paths else (None, None)
        records[kind] = diff_records(old, new)
        if kind == "products" and path in paths:
            records[kind].urls = _product_urls(old, new)
    theme = diff_theme(_head_json(repo, THEME_FILE), _work_json(repo, THEME_FILE)) if THEME_FILE in paths else []

    images = ImageChanges()
    for code, path in changed:
        if not path.startswith(IMAGES_DIR) or path == MANIFEST:
            continue
        if code == "??" or "A" in code:
            images.added += 1
        elif "D" in code:
            images.removed += 1
        else:
            images.changed += 1
    other = sorted(p for p in paths if p not in SUMMARISED and not p.startswith(IMAGES_DIR))
    return ExportChanges(records, theme, images, other, paths)


# --- wording --------------------------------------------------------------------------------

def _named(count_label: str, titles: list[str]) -> str:
    return f"{len(titles)} {count_label} ({', '.join(titles)})" if titles else ""


def format_summary(changes: ExportChanges) -> str:
    if changes.empty:
        return "No store content changed since the last publish (export/ matches the last commit)."
    lines = ["Store content changed since the last publish:"]
    for kind, record in changes.records.items():
        parts = [p for p in (_named("changed", record.changed), _named("added", record.added),
                             _named("removed", record.removed)) if p]
        lines.append(f"- {kind.capitalize()}: {', '.join(parts) if parts else 'no changes'}")
    lines.append(f"- Theme editor content: {', '.join(changes.theme) if changes.theme else 'no changes'}")
    img = changes.images
    lines.append(f"- Images: {img.added} added, {img.changed} changed, {img.removed} removed"
                 if img.total else "- Images: no changes")
    if changes.other_files:
        lines.append(f"- Other export files changed: {', '.join(changes.other_files)}")
    lines.append("")
    lines.append("Pages to spot-check after publishing:")
    lines.append(f"  {SITE_URL}/")
    lines += [f"  {url}" for url in changes.records["products"].urls]
    return "\n".join(lines)


def commit_message(changes: ExportChanges, today: date, name: str) -> str:
    lines = [f"Publish {today.isoformat()} by {name}", "",
             f"Store content exported from Shopify and published to {SITE_URL}.", ""]
    for kind, record in changes.records.items():
        if not record.total:
            lines.append(f"{kind.capitalize()}: none")
            continue
        titles = record.changed + record.added + record.removed
        lines.append(f"{kind.capitalize()}: {len(record.changed)} changed, {len(record.added)} added, "
                     f"{len(record.removed)} removed ({', '.join(titles)})")
    lines.append(f"Theme editor content: {', '.join(changes.theme) if changes.theme else 'none'}")
    img = changes.images
    lines.append(f"Images: {img.added} added, {img.changed} changed, {img.removed} removed"
                 if img.total else "Images: none")
    if changes.other_files:
        lines.append(f"Other export files changed: {', '.join(changes.other_files)}")
    return "\n".join(lines) + "\n"


# --- the commit -----------------------------------------------------------------------------

def commit(repo: Path, today: date) -> str:
    """Commit export/ only; return the new commit's short hash, or '' when nothing changed."""
    name = _git(repo, "config", "user.name", check=False).stdout.decode("utf-8").strip()
    if not name:
        raise ChangesError('git does not know your name yet: run git config --global user.name "Your Name" '
                           'and git config --global user.email "you@example.com", then try again')
    outside = [path for _, path in status(repo) if not (path == EXPORT or path.startswith(EXPORT + "/"))]
    if outside:
        raise ChangesError(f"files outside export/ are changed: {', '.join(outside[:10])}. A publish commit "
                           "holds the export only: do not commit or discard them; contact the owner")
    changes = summarise(repo)
    if changes.empty:
        return ""
    _git(repo, "add", "--", EXPORT)
    with tempfile.TemporaryDirectory(prefix="rosart-commit-") as tmp:
        message_file = Path(tmp) / "message.txt"
        message_file.write_bytes(commit_message(changes, today, name).encode("utf-8"))
        _git(repo, "commit", "-q", "-F", str(message_file))
    return _git(repo, "rev-parse", "--short", "HEAD").stdout.decode("utf-8").strip()


def main(argv: Optional[list[str]] = None, repo: Optional[Path] = None, today: Optional[date] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--commit", action="store_true", help="commit export/ only, after a successful publish")
    args = parser.parse_args(argv)
    repo = repo or REPO_ROOT
    try:
        if not args.commit:
            print(format_summary(summarise(repo)))
            return 0
        sha = commit(repo, today or date.today())
    except (ChangesError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"export changes: {exc}", file=sys.stderr)
        return 1
    if not sha:
        print("Nothing to commit: export/ matches the last commit.")
    else:
        print(f"Committed the export as {sha} on this PC. Never push: the owner pushes it.")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
