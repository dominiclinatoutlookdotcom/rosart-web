"""The short count line of a change summary, for the history, the job page and Slack (spec 006 FR-006, FR-013)."""
from __future__ import annotations

from typing import Any

PLURALS = (("products", "product", "products"), ("pages", "page", "pages"),
           ("collections", "collection", "collections"), ("menus", "menu", "menus"), ("blogs", "blog", "blogs"),
           ("theme_areas", "theme area", "theme areas"))


def change_counts(summary: dict[str, Any]) -> str:
    """The short count line of a summary, for example ``2 products, 1 page changed`` (at most 200 characters)."""
    parts = []
    for key, one, many in PLURALS:
        entry = (summary.get("changed") or {}).get(key) or {}
        total = sum(len(entry.get(kind) or []) for kind in ("changed", "added", "removed"))
        if total:
            parts.append(f"{total} {one if total == 1 else many}")
    images = summary.get("images") or {}
    total_images = sum(images.get(kind, 0) for kind in ("changed", "added", "removed"))
    if total_images:
        parts.append(f"{total_images} {'image' if total_images == 1 else 'images'}")
    return (", ".join(parts) + " changed")[:200] if parts else "no content changes"
