"""Image rules for the export: which hosts may be fetched, local filenames, and references.

Pure functions only; the network fetch lives in ``downloads.py``.
"""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit

ALLOWED_HOST = "cdn.shopify.com"
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".avif", ".heic"}

_SHOP_IMAGE_RE = re.compile(r"shopify://shop_images/([^\s\"'<>)?#]+)")
_CDN_URL_RE = re.compile(r"(?:https?:)?//cdn\.shopify\.com/s/files/[^\s\"'<>)\\]+")
_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


class DisallowedHost(ValueError):
    """A download was requested from a host other than the Shopify CDN."""


def assert_allowed_host(url: str) -> None:
    """Allow only ``https://cdn.shopify.com/...``; raise :class:`DisallowedHost` otherwise."""
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.hostname != ALLOWED_HOST or parts.username or parts.port:
        raise DisallowedHost(f"refusing to download from {url!r}: only https://{ALLOWED_HOST}/ is allowed")


def canonical_url(url: str) -> str:
    """The URL without query or fragment: ``?v=`` is a cache-buster, not a different file."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"


def filename_from_url(url: str) -> str:
    """The original filename: last path segment, percent-decoded, made safe for Windows."""
    name = unquote(PurePosixPath(urlsplit(url).path).name)
    name = _UNSAFE_FILENAME_CHARS.sub("_", name).strip(" .")
    return name or "image"


def assign_local_names(urls: Iterable[str]) -> dict[str, str]:
    """Map each canonical URL to a unique local filename, deterministically.

    URLs that differ only in their query are one file. Canonical URLs are processed in sorted
    order, Shopify Files URLs first; each original filename goes to the first URL that has it, and later URLs with the same
    name (compared case-insensitively, as Windows and macOS do) get ``<stem>-2<ext>``, ``-3`` ...,
    skipping any name another URL has as its original.
    """
    # Shopify Files (``.../files/<name>``) is where theme ``shop_images`` names point, so those
    # URLs claim original names before copies under ``/products/`` or ``/collections/``.
    canonicals = sorted({canonical_url(u) for u in urls},
                        key=lambda c: (PurePosixPath(urlsplit(c).path).parent.name != "files", c))
    originals = {c: filename_from_url(c) for c in canonicals}

    names: dict[str, str] = {}
    taken: set[str] = set()
    duplicates: list[str] = []
    for c in canonicals:
        key = originals[c].lower()
        if key in taken:
            duplicates.append(c)
            continue
        names[c] = originals[c]
        taken.add(key)

    for c in duplicates:
        path = PurePosixPath(originals[c])
        stem, suffix = path.stem, path.suffix
        counter = 2
        while f"{stem}-{counter}{suffix}".lower() in taken:
            counter += 1
        names[c] = f"{stem}-{counter}{suffix}"
        taken.add(names[c].lower())
    return names


def _strings(node: Any) -> Iterable[str]:
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from _strings(value)


def find_shop_image_refs(node: Any) -> list[str]:
    """Sorted, unique ``shopify://shop_images/<name>`` names anywhere in a JSON value."""
    found = {m.group(1) for text in _strings(node) for m in _SHOP_IMAGE_RE.finditer(text)}
    return sorted(found)


def find_cdn_image_urls(node: Any) -> list[str]:
    """Shopify CDN image URLs embedded in strings (HTML bodies, custom Liquid), in order, unique."""
    found: list[str] = []
    for text in _strings(node):
        for match in _CDN_URL_RE.finditer(text):
            url = match.group(0)
            if url.startswith("//"):
                url = "https:" + url
            elif url.startswith("http://"):
                url = "https://" + url[len("http://"):]
            if PurePosixPath(urlsplit(url).path).suffix.lower() in IMAGE_EXTENSIONS and url not in found:
                found.append(url)
    return found


def resolve_shop_images(names: Iterable[str], files: list[dict]) -> tuple[dict[str, str], list[str]]:
    """Map theme ``shop_images`` names to file URLs via each file's ``filename``.

    Exact filename match first, then case-insensitive. Returns ``(resolved, unresolved)``.
    """
    exact = {f["filename"]: f["url"] for f in files if f.get("filename") and f.get("url")}
    folded: dict[str, str] = {}
    for filename, url in sorted(exact.items()):
        folded.setdefault(filename.lower(), url)

    resolved: dict[str, str] = {}
    unresolved: list[str] = []
    for name in sorted(set(names)):
        url = exact.get(name) or folded.get(name.lower())
        if url:
            resolved[name] = url
        else:
            unresolved.append(name)
    return resolved, unresolved
