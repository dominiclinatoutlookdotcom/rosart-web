"""Tests for the export change summary and the publish commit, against a real throwaway git repo."""
import json
import subprocess
from datetime import date
from pathlib import Path

import pytest

from export_changes import main, summarise

TODAY = date(2026, 11, 7)


def git(repo: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)
    return completed.stdout.decode("utf-8")


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


PRODUCTS = [
    {"id": "gid://shopify/Product/1", "handle": "rose-water", "title": "Rose Water", "descriptionHtml": "<p>A</p>"},
    {"id": "gid://shopify/Product/2", "handle": "rose-cream", "title": "Rose Cream", "descriptionHtml": "<p>B</p>"},
    {"id": "gid://shopify/Product/3", "handle": "rose-oil", "title": "Rose Oil", "descriptionHtml": "<p>C</p>"},
]
PAGES = [
    {"id": "gid://shopify/Page/1", "handle": "contact", "title": "Contact", "bodyHtml": "<p>info@rosart.com.au</p>"},
    {"id": "gid://shopify/Page/2", "handle": "our-story", "title": "Our Story", "bodyHtml": "<p>Story</p>"},
]
COLLECTIONS = [{"id": "gid://shopify/Collection/1", "handle": "face-care", "title": "Face Care", "productHandles": []}]
THEME = {"global_settings": {"logo": "logo.png"},
         "section_groups": {"sections/footer-group.json": {"text": "Footer"},
                            "sections/header-group.json": {"text": "Header"}},
         "templates": {"templates/index.json": {"text": "Welcome"},
                       "templates/page.our-story.json": {"text": "Story"}}}


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    # Isolate from the developer's own git config (hooks, signing, identity).
    empty = tmp_path / "empty.gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    root = tmp_path / "rosart-web"
    root.mkdir()
    git(root, "init", "-q", "-b", "main")
    git(root, "config", "user.name", "Test Staff")
    git(root, "config", "user.email", "staff@example.com")
    git(root, "config", "core.autocrlf", "false")
    (root / "README.md").write_text("site\n", encoding="utf-8")
    write_json(root / "export/shopify/products.json", PRODUCTS)
    write_json(root / "export/shopify/pages.json", PAGES)
    write_json(root / "export/shopify/collections.json", COLLECTIONS)
    write_json(root / "export/shopify/menus.json", [{"id": "m1", "handle": "main-menu", "title": "Main menu", "items": []}])
    write_json(root / "export/shopify/blogs.json", [])
    write_json(root / "export/shopify/files.json", [{"id": "f1", "alt": ""}])
    write_json(root / "export/theme-content.json", THEME)
    (root / "export/images").mkdir(parents=True)
    (root / "export/images/a.jpg").write_bytes(b"aaa")
    (root / "export/images/b.jpg").write_bytes(b"bbb")
    write_json(root / "export/images/manifest.json", {"images": {"a.jpg": {}, "b.jpg": {}}})
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "Initial export")
    return root


def edit_products(repo: Path) -> None:
    products = json.loads((repo / "export/shopify/products.json").read_text(encoding="utf-8"))
    products[0]["descriptionHtml"] = "<p>A, now with rose</p>"          # changed
    products = [p for p in products if p["handle"] != "rose-oil"]       # removed
    products.append({"id": "gid://shopify/Product/4", "handle": "rose-mist", "title": "Rosé Mist",
                     "descriptionHtml": "<p>D</p>"})                    # added
    write_json(repo / "export/shopify/products.json", products)


# --- the summary ----------------------------------------------------------------------------

def test_no_changes_says_so(repo, capsys):
    assert summarise(repo).empty
    assert main([], repo=repo, today=TODAY) == 0
    assert "No store content changed" in capsys.readouterr().out


def test_products_added_changed_and_removed_are_named_by_title(repo):
    edit_products(repo)
    products = summarise(repo).records["products"]
    assert products.changed == ["Rose Water"]
    assert products.added == ["Rosé Mist"]
    assert products.removed == ["Rose Oil"]


def test_summary_lists_the_pages_to_spot_check(repo, capsys):
    edit_products(repo)
    main([], repo=repo, today=TODAY)
    out = capsys.readouterr().out
    assert "Products: 1 changed (Rose Water), 1 added (Rosé Mist), 1 removed (Rose Oil)" in out
    assert "https://rosart.com.au/" in out
    assert "https://rosart.com.au/products/rose-water/" in out
    assert "https://rosart.com.au/products/rose-mist/" in out
    assert "https://rosart.com.au/products/rose-oil/" not in out


def test_page_and_collection_changes_are_named(repo):
    pages = json.loads((repo / "export/shopify/pages.json").read_text(encoding="utf-8"))
    pages[0]["bodyHtml"] = "<p>Call us</p>"
    write_json(repo / "export/shopify/pages.json", pages)
    write_json(repo / "export/shopify/collections.json", [{**COLLECTIONS[0], "title": "Face & Body"}])
    changes = summarise(repo)
    assert changes.records["pages"].changed == ["Contact"]
    assert changes.records["collections"].changed == ["Face & Body"]
    assert changes.records["products"].changed == []


def test_theme_editor_changes_are_named_in_plain_words(repo):
    theme = json.loads((repo / "export/theme-content.json").read_text(encoding="utf-8"))
    theme["templates"]["templates/index.json"]["text"] = "Welcome to Rosart"
    theme["section_groups"]["sections/footer-group.json"]["text"] = "New footer"
    write_json(repo / "export/theme-content.json", theme)
    assert summarise(repo).theme == ["footer", "homepage"]


def test_images_are_counted_without_the_manifest(repo):
    (repo / "export/images/a.jpg").write_bytes(b"a2")       # changed
    (repo / "export/images/b.jpg").unlink()                  # removed
    (repo / "export/images/c.jpg").write_bytes(b"ccc")      # added
    (repo / "export/images/d.jpg").write_bytes(b"ddd")      # added
    write_json(repo / "export/images/manifest.json", {"images": {"a.jpg": {"v": 2}}})
    images = summarise(repo).images
    assert (images.added, images.changed, images.removed) == (2, 1, 1)


def test_other_export_files_are_listed(repo):
    write_json(repo / "export/shopify/files.json", [{"id": "f1", "alt": "A rose"}])
    assert summarise(repo).other_files == ["export/shopify/files.json"]


# --- the commit -----------------------------------------------------------------------------

def test_commit_records_only_the_export_with_a_counted_message(repo, capsys):
    edit_products(repo)
    (repo / "export/images/c.jpg").write_bytes(b"ccc")
    before = git(repo, "rev-parse", "HEAD").strip()

    assert main(["--commit"], repo=repo, today=TODAY) == 0

    assert git(repo, "rev-parse", "HEAD~1").strip() == before
    message = git(repo, "log", "-1", "--format=%B")
    subject, _, body = message.partition("\n")
    assert subject == "Publish 2026-11-07 by Test Staff"
    assert "Products: 1 changed, 1 added, 1 removed (Rose Water, Rosé Mist, Rose Oil)" in body
    assert "Pages: none" in body
    assert "Images: 1 added, 0 changed, 0 removed" in body
    files = git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert sorted(files) == ["export/images/c.jpg", "export/shopify/products.json"]
    assert git(repo, "status", "--porcelain") == ""
    assert "never push" in capsys.readouterr().out.lower()


def test_commit_with_nothing_changed_makes_no_commit(repo, capsys):
    before = git(repo, "rev-parse", "HEAD").strip()
    assert main(["--commit"], repo=repo, today=TODAY) == 0
    assert git(repo, "rev-parse", "HEAD").strip() == before
    assert "nothing to commit" in capsys.readouterr().out.lower()


def test_commit_refuses_when_files_outside_export_changed(repo, capsys):
    edit_products(repo)
    (repo / "README.md").write_text("edited\n", encoding="utf-8")
    before = git(repo, "rev-parse", "HEAD").strip()
    assert main(["--commit"], repo=repo, today=TODAY) == 1
    assert git(repo, "rev-parse", "HEAD").strip() == before
    assert git(repo, "diff", "--cached", "--name-only") == ""
    err = capsys.readouterr().err
    assert "README.md" in err and "owner" in err


def test_commit_refuses_without_a_git_name(repo, capsys):
    edit_products(repo)
    git(repo, "config", "--unset", "user.name")
    assert main(["--commit"], repo=repo, today=TODAY) == 1
    assert 'git config --global user.name' in capsys.readouterr().err
