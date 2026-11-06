"""Tests for the theme pull that refreshes export/theme/ before the export (fake CLI, no network)."""
import subprocess
from pathlib import Path

import pytest

from pull_theme import DEFAULT_STORE, DEFAULT_THEME, main, pull_theme
from shopify_cli import ShopifyCliError

REPO_ROOT = Path(__file__).resolve().parent.parent


class FakeRun:
    def __init__(self, returncode=0):
        self.calls = []
        self.returncode = returncode

    def __call__(self, command, **kwargs):
        self.calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, self.returncode)


def test_pulls_the_rosart_theme_into_the_given_folder(tmp_path):
    run = FakeRun()
    pull_theme(tmp_path / "theme", executable="shopify.cmd", run=run)
    [(command, kwargs)] = run.calls
    assert command == ["shopify.cmd", "theme", "pull", "--store", "rosart-mhfmyuxq.myshopify.com",
                       "--theme", "201570156625", "--path", str((tmp_path / "theme").resolve()),
                       "--no-color"]
    assert (DEFAULT_STORE, DEFAULT_THEME) == ("rosart-mhfmyuxq.myshopify.com", "201570156625")


def test_runs_from_a_neutral_folder_outside_the_repo(tmp_path):
    run = FakeRun()
    pull_theme(tmp_path / "theme", executable="shopify", run=run)
    [(_, kwargs)] = run.calls
    cwd = Path(kwargs["cwd"]).resolve()
    assert REPO_ROOT not in [cwd, *cwd.parents]


def test_a_relative_path_is_made_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run = FakeRun()
    pull_theme(Path("export/theme"), executable="shopify", run=run)
    [(command, _)] = run.calls
    assert command[command.index("--path") + 1] == str(tmp_path.resolve() / "export" / "theme")


def test_a_failed_pull_raises(tmp_path):
    with pytest.raises(ShopifyCliError, match="exited 1"):
        pull_theme(tmp_path / "theme", executable="shopify", run=FakeRun(returncode=1))


def test_main_reports_a_failed_pull_as_exit_1(tmp_path, capsys):
    assert main(["--theme-dir", str(tmp_path / "theme")], executable="shopify",
                run=FakeRun(returncode=2)) == 1
    assert "theme pull" in capsys.readouterr().err
