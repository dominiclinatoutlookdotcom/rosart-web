"""Read the live theme through the Admin API into a folder (spec 006, hand-off R-2).

The read-only replacement for ``shopify theme pull`` on the publisher box: it needs only the
``read_themes`` scope. ``read_theme`` pages through ``theme(id).files``, rebuilds every file byte
for byte (Text, Base64 and Url bodies), verifies the size and MD5 of each, and swaps a freshly
written folder into ``dest`` by rename, so ``dest`` mirrors the theme exactly. Nothing is touched
unless every check passed. Url bodies are fetched only from ``THEME_URL_HOSTS``.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import os
import shutil
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from shopify_admin import ShopifyAdminError
from shopify_cli import MAX_PAGES, Runner

# The hosts Url bodies may come from: one constant, extended only by the owner's ruling on V-3
# (a reviewed image release), never at run time (research R-3, C12).
THEME_URL_HOSTS = ("cdn.shopify.com",)

# Top-level folders of a Shopify theme; files directly in the root are allowed too.
THEME_FOLDERS = ("assets", "blocks", "config", "layout", "locales", "sections", "snippets", "templates")

QUERY_THEME_FILES = """query ThemeFiles($id: ID!, $after: String) {
  theme(id: $id) {
    id name role processing
    files(first: 250, after: $after) {
      nodes { filename size checksumMd5 contentType body {
        ... on OnlineStoreThemeFileBodyText { content }
        ... on OnlineStoreThemeFileBodyBase64 { contentBase64 }
        ... on OnlineStoreThemeFileBodyUrl { url } } }
      pageInfo { hasNextPage endCursor }
      userErrors { code filename }
    }
  }
}"""


@dataclass(frozen=True)
class ThemeFileEntry:
    filename: str
    sha256: str
    size: int


@dataclass(frozen=True)
class ThemeManifest:
    """What was written: SHA-256 and size per file, sorted by name (the V-4 comparison input)."""

    files: tuple[ThemeFileEntry, ...]

    def as_list(self) -> list[dict]:
        return [{"filename": f.filename, "sha256": f.sha256, "size": f.size} for f in self.files]


def theme_gid(theme_id: str) -> str:
    return theme_id if theme_id.startswith("gid://") else f"gid://shopify/OnlineStoreTheme/{theme_id}"


def _integrity(message: str) -> ShopifyAdminError:
    return ShopifyAdminError("theme-checksum", message)


def _check_name(name: str) -> None:
    parts = name.split("/")
    unsafe = (not name or name.startswith("/") or "\\" in name or "\x00" in name
              or any(part in ("", ".", "..") or ":" in part for part in parts)
              or (len(parts) > 1 and parts[0] not in THEME_FOLDERS))
    if unsafe:
        raise _integrity(f"the theme has a file with an unsafe name: {name!r}")


def _check_host(url: str, name: str) -> None:
    parsed = urllib.parse.urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or host not in THEME_URL_HOSTS or parsed.port not in (None, 443):
        raise ShopifyAdminError("egress-refused",
                                f"refusing to download theme file {name} from {host or 'an unknown host'}: "
                                f"allowed hosts are {', '.join(THEME_URL_HOSTS)}")


def _fetch_pages(run: Runner, theme_id: str, require_main: bool) -> list[dict]:
    nodes: list[dict] = []
    seen: set[str] = set()
    cursor = None
    for _ in range(MAX_PAGES):
        theme = run(QUERY_THEME_FILES, {"id": theme_id, "after": cursor}).get("theme")
        if not theme:
            raise ShopifyAdminError("theme-missing", f"the theme {theme_id} was not found in the store")
        if require_main and theme.get("role") != "MAIN":
            raise ShopifyAdminError("theme-not-live", f"the theme {theme_id} is not the live theme "
                                    f"(role {theme.get('role')})")
        files = theme.get("files") or {}
        errors = files.get("userErrors") or []
        if errors:
            detail = "; ".join(f"{e.get('code')} {e.get('filename') or ''}".strip() for e in errors)
            raise ShopifyAdminError("shopify-access", f"Shopify could not read the theme files: {detail}")
        nodes.extend(files.get("nodes") or [])
        page_info = files.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return nodes
        cursor = page_info.get("endCursor")
        if not cursor or cursor in seen:
            raise ShopifyAdminError("shopify-access", "paging the theme files returned a repeated or empty cursor")
        seen.add(cursor)
    raise ShopifyAdminError("shopify-access", f"paging the theme files exceeded {MAX_PAGES} pages")


def _body_bytes(node: dict, fetch_url: Callable[[str], bytes]) -> bytes:
    name = node["filename"]
    body = node.get("body") or {}
    if body.get("content") is not None:
        return body["content"].encode("utf-8")
    if body.get("contentBase64") is not None:
        try:
            return base64.b64decode(body["contentBase64"], validate=True)
        except (binascii.Error, ValueError):
            raise _integrity(f"theme file {name} has an invalid Base64 body") from None
    if body.get("url"):
        _check_host(body["url"], name)
        try:
            return fetch_url(body["url"])
        except ShopifyAdminError:
            raise
        except Exception as exc:  # the URL is short-lived but may be signed: name the type only
            raise ShopifyAdminError("shopify-access",
                                    f"could not download theme file {name} ({type(exc).__name__})") from None
    raise _integrity(f"theme file {name} came back without a body")


def _parse_size(raw: object, name: str) -> int:
    """``size`` is UnsignedInt64: Shopify sends a JSON string of ASCII digits (an int is tolerated)."""
    if isinstance(raw, int) and not isinstance(raw, bool) and raw >= 0:
        return raw
    if isinstance(raw, str) and raw.isascii() and raw.isdigit():
        return int(raw)
    raise _integrity(f"theme file {name} has an invalid size {raw!r}")


def _verify(node: dict, data: bytes) -> None:
    name = node["filename"]
    size = _parse_size(node.get("size"), name)
    if len(data) != size:
        raise _integrity(f"theme file {name} has {len(data)} bytes, Shopify says {size}")
    checksum = node.get("checksumMd5")
    if checksum is not None and hashlib.md5(data).hexdigest() != str(checksum).lower():
        raise _integrity(f"theme file {name} does not match its MD5 checksum")


def _swap(staging: Path, dest: Path) -> None:
    previous = dest.parent / f".{dest.name}.old-{os.getpid()}"
    had_old = dest.exists()
    if had_old:
        os.replace(dest, previous)
    try:
        os.replace(staging, dest)
    except OSError:
        if had_old:
            os.replace(previous, dest)
        raise
    if had_old:
        shutil.rmtree(previous, ignore_errors=True)


def read_theme(run: Runner, fetch_url: Callable[[str], bytes], theme_id: str, dest: Path,
               require_main: bool = True) -> ThemeManifest:
    """Write the theme's files into ``dest`` (mirrored); return the SHA-256 manifest."""
    dest = Path(dest)
    nodes = _fetch_pages(run, theme_gid(theme_id), require_main)
    for node in nodes:
        _check_name(node.get("filename") or "")
        url = (node.get("body") or {}).get("url")
        if url:
            _check_host(url, node["filename"])   # refuse before any download starts

    contents: dict[str, bytes] = {}
    for node in nodes:
        data = _body_bytes(node, fetch_url)
        _verify(node, data)
        contents[node["filename"]] = data

    dest.parent.mkdir(parents=True, exist_ok=True)
    staging = dest.parent / f".{dest.name}.new-{os.getpid()}"
    shutil.rmtree(staging, ignore_errors=True)
    try:
        for name, data in contents.items():
            target = staging / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        staging.mkdir(parents=True, exist_ok=True)
        _swap(staging, dest)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    return ThemeManifest(tuple(ThemeFileEntry(name, hashlib.sha256(data).hexdigest(), len(data))
                               for name, data in sorted(contents.items())))
