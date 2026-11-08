"""``python -m publisher seed-export-repo`` (spec 006 T059/T060; research R-9, A-8).

Seam: ``publisher.__main__.main`` over a real empty bare repository and a folder standing in for the image's /app.
"""
import pytest

from flow_fakes import empty_bare_repo, git, head_files, write_tree

FILES = {"export/shopify/products.json": "[]", "export/theme-content.json": "{}",
         "export/theme/layout/theme.liquid": "licensed", "src/site.ts": "not exported"}


@pytest.fixture
def seed_env(tmp_path, monkeypatch):
    bare = empty_bare_repo(tmp_path / "export.git")
    app = tmp_path / "app"
    write_tree(app, FILES)
    monkeypatch.setenv("PUBLISHER_EXPORT_REPO", str(bare))
    monkeypatch.setenv("PUBLISHER_CODE_COMMIT", "deadbeef")
    monkeypatch.setenv("PUBLISHER_TMP", str(tmp_path / "tmp"))
    return bare, app


def test_seed_makes_one_commit_from_the_image_export_and_says_so(seed_env, capsys):
    from publisher.__main__ import main

    bare, app = seed_env

    assert main(["seed-export-repo", "--app-root", str(app)]) == 0

    assert git("--git-dir", str(bare), "rev-list", "--count", "HEAD").strip() == "1"
    assert sorted(head_files(bare)) == ["export/shopify/products.json", "export/theme-content.json"]
    assert "seeded" in capsys.readouterr().out.lower()


def test_seed_refuses_a_second_time_and_exits_non_zero(seed_env, capsys):
    from publisher.__main__ import main

    bare, app = seed_env
    assert main(["seed-export-repo", "--app-root", str(app)]) == 0
    capsys.readouterr()

    assert main(["seed-export-repo", "--app-root", str(app)]) == 1

    assert "not empty" in capsys.readouterr().err
    assert git("--git-dir", str(bare), "rev-list", "--count", "HEAD").strip() == "1"
