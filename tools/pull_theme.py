"""Refresh ``export/theme/`` with ``shopify theme pull`` (read-only on the store).

The theme holds the owner-written copy edited in the Shopify theme editor (homepage, Our Story,
header, footer). The export reads it from ``export/theme/`` into ``theme-content.json``, so the
theme is pulled before every export. The pull mirrors the remote theme into the folder.

Usage::

    python tools/pull_theme.py [--store DOMAIN] [--theme ID] [--theme-dir DIR]
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable, Optional

from shopify_cli import ShopifyCliError

DEFAULT_STORE = "rosart-mhfmyuxq.myshopify.com"
DEFAULT_THEME = "201570156625"
REPO_ROOT = Path(__file__).resolve().parent.parent


def pull_theme(theme_dir: Path, store: str = DEFAULT_STORE, theme: str = DEFAULT_THEME,
               executable: Optional[str] = None,
               run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
    """Pull ``theme`` from ``store`` into ``theme_dir``; output goes straight to the terminal so a
    login prompt or progress is visible."""
    executable = executable or shutil.which("shopify")
    if not executable:
        raise ShopifyCliError("the `shopify` CLI is not on PATH")
    target = Path(theme_dir).resolve()
    command = [executable, "theme", "pull", "--store", store, "--theme", theme,
               "--path", str(target), "--no-color"]
    # A neutral cwd: the CLI must not pick up a project or theme config from the repo.
    with tempfile.TemporaryDirectory(prefix="rosart-theme-pull-") as tmp:
        completed = run(command, cwd=tmp)
    if completed.returncode != 0:
        raise ShopifyCliError(f"shopify theme pull exited {completed.returncode}")


def main(argv: Optional[list[str]] = None, executable: Optional[str] = None,
         run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", default=DEFAULT_STORE)
    parser.add_argument("--theme", default=DEFAULT_THEME, help="theme ID")
    parser.add_argument("--theme-dir", type=Path, default=REPO_ROOT / "export" / "theme")
    args = parser.parse_args(argv)
    try:
        pull_theme(args.theme_dir, args.store, args.theme, executable=executable, run=run)
    except ShopifyCliError as exc:
        print(f"theme pull: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
