"""Validates an SEO fix plan (seo/plan.json) before the owner approves it.

Rules (owner's listing checklist, sections 2 and 3):
- SEO title at most 60 characters; SEO description 120 to 155 characters; both plain text.
- SEO titles and SEO descriptions unique across every resource (changes and kept values).
- No number, unit, material, brand, place or claim word in a new value unless that entry's own
  source text supports it ("Rosart", "Bulgaria(n)", "Valley of Roses", "Damask", "organic" included).
- No SKU in any new value.
- Alt text non-empty, plain text, and distinct per product.
- Collection descriptions are 2 or 3 sentences; categories are taxonomy category gids.
- Every change has sources and actually changes something.

Usage: python tools/seo_check.py [seo/plan.json]   (exit 0 when clean, 1 with one line per error)
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

SEO_TITLE_MAX = 60
SEO_DESCRIPTION_RANGE = (120, 155)

PLAIN_TEXT_FIELDS = {"seo.title", "seo.description", "alt", "image.altText"}
FACT_CHECKED_FIELDS = PLAIN_TEXT_FIELDS | {"descriptionHtml"}

UNITS = r"(?:fl\.?\s?oz|ml|mg|kg|g|l|oz|pcs|pc|capsules?|%)"
NUMBER_RE = re.compile(rf"(?<![\w.])(\d+(?:\.\d+)?)\s*({UNITS})?(?!\w)", re.IGNORECASE)
UNIT_ALIASES = {"capsule": "capsules", "pc": "pcs"}

# Each claim word in a new value needs the paired pattern in that entry's own source text.
CLAIMS: dict[str, tuple[str, str]] = {
    "rosart": (r"\brosart\b", r"\brosart\b"),
    "bulgaria": (r"\bbulgaria(?:n)?\b", r"\bbulgaria"),
    "valley of roses": (r"\bvalley of roses\b", r"\bvalley of roses\b"),
    "damask": (r"\bdamask\b", r"\bdamask\b"),
    "damascena": (r"\bdamascena\b", r"\bdamascena\b"),
    "organic": (r"\borganic\b", r"\borganic\b"),
    "certified": (r"\bcertified\b", r"\bcertified\b"),
    "handmade": (r"\bhand-?made\b", r"\bhand-?made\b"),
}
BRANDS_AND_PLACES = [
    "orizen", "artec", "usda", "ceres", "gmp", "minervabelle", "deniva", "esprit", "shopify",
    "australia", "australian", "melbourne", "zlatosel", "hong kong", "europe", "european", "eu",
]
MATERIALS = [
    "glass", "gold", "golden", "rose-gold", "amber", "copper", "brass", "plastic", "wood", "wooden",
    "paper", "ceramic", "porcelain", "metal", "silver", "aluminium", "aluminum", "bamboo", "cotton",
    "silk", "linen", "lace", "stone", "marble", "leather", "steel", "iron", "crystal", "velvet",
]


def _text(html: str) -> str:
    """Tag-free text of an HTML value, for sentence counting and fact checks."""
    return re.sub(r"<[^>]+>", " ", html or "").replace("&amp;", "&")


def _token(number: str, unit: str) -> str:
    unit = re.sub(r"\s", "", (unit or "").lower())
    return number + UNIT_ALIASES.get(unit, unit)


def _numbers(text: str) -> set[str]:
    """Every number in the text, with its unit and bare, normalised ("50 ML" -> "50ml", "50")."""
    found = set()
    for number, unit in NUMBER_RE.findall(text):
        found.add(_token(number, unit))
        found.add(number)
    return found


def _source_text(entry: dict[str, Any]) -> str:
    return " ".join(str(s.get("text", "")) for s in entry.get("sources") or [])


def _value(entry: dict[str, Any]) -> str:
    return str(entry["after"] if "after" in entry else entry.get("value", ""))


def _label(entry: dict[str, Any]) -> str:
    suffix = f" [{entry['file']}]" if entry.get("file") else ""
    return f"{entry.get('resource')} {entry.get('handle')} {entry.get('field')}{suffix}"


def _fact_errors(entry: dict[str, Any]) -> list[str]:
    value = _value(entry)
    plain = _text(value) if entry.get("field") == "descriptionHtml" else value
    source = _source_text(entry)
    low_value, low_source = plain.lower(), source.lower()
    label = _label(entry)
    errors = []

    source_numbers = _numbers(source)
    for number, unit in NUMBER_RE.findall(plain):
        token = _token(number, unit)
        if token not in source_numbers:
            errors.append(f"{label}: number '{token}' is not in the source text")

    for name, (value_pattern, source_pattern) in CLAIMS.items():
        if re.search(value_pattern, low_value) and not re.search(source_pattern, low_source):
            errors.append(f"{label}: claim '{name}' is not supported by the source text")

    for word in BRANDS_AND_PLACES + MATERIALS:
        pattern = rf"(?<![\w-]){re.escape(word)}(?![\w-])"
        if re.search(pattern, low_value) and not re.search(pattern, low_source):
            kind = "material" if word in MATERIALS else "brand/place"
            errors.append(f"{label}: {kind} '{word}' is not in the source text")
    return errors


def _entry_errors(entry: dict[str, Any], skus: Iterable[str], is_change: bool) -> list[str]:
    field = entry.get("field")
    value = _value(entry)
    label = _label(entry)
    errors = []

    if is_change:
        if not entry.get("sources"):
            errors.append(f"{label}: no sources recorded")
        if entry.get("before") == value:
            errors.append(f"{label}: no-op change (before equals after)")

    if field in PLAIN_TEXT_FIELDS:
        if not value.strip():
            errors.append(f"{label}: empty value")
        if re.search(r"<[^>]+>|&[#\w]+;", value):
            errors.append(f"{label}: must be plain text (no HTML tags or entities)")
    if field == "seo.title" and len(value) > SEO_TITLE_MAX:
        errors.append(f"{label}: seo.title is {len(value)} characters, the limit is {SEO_TITLE_MAX}")
    if field == "seo.description":
        low, high = SEO_DESCRIPTION_RANGE
        if not low <= len(value) <= high:
            errors.append(f"{label}: seo.description is {len(value)} characters, must be {low}-{high}")
    if field == "descriptionHtml" and entry.get("resource") == "collection":
        sentences = [s for s in re.split(r"(?<=[.!?])\s+", _text(value).strip()) if s.strip()]
        if not 2 <= len(sentences) <= 3:
            errors.append(f"{label}: collection description has {len(sentences)} sentences, must be 2-3")
    if field == "category" and not value.startswith("gid://shopify/TaxonomyCategory/"):
        errors.append(f"{label}: category must be a gid://shopify/TaxonomyCategory/ id")

    if field in FACT_CHECKED_FIELDS:
        if is_change:
            errors.extend(_fact_errors(entry))
        for sku in skus:
            if re.search(rf"(?<!\w){re.escape(sku)}(?!\w)", value, re.IGNORECASE):
                errors.append(f"{label}: contains SKU {sku}")
    return errors


def check_plan(plan: dict[str, Any]) -> list[str]:
    """All rule violations in the plan, one message each; an empty list means the plan is clean."""
    skus = plan.get("skus") or []
    changes = plan.get("changes") or []
    kept = plan.get("kept") or []
    errors: list[str] = []

    for entry in changes:
        errors.extend(_entry_errors(entry, skus, is_change=True))
    for entry in kept:
        errors.extend(_entry_errors(entry, skus, is_change=False))

    final = changes + kept
    for field in ("seo.title", "seo.description"):
        seen: dict[str, list[str]] = defaultdict(list)
        for entry in final:
            if entry.get("field") == field and _value(entry).strip():
                seen[_value(entry).strip().lower()].append(f"{entry.get('resource')} {entry.get('handle')}")
        for value, owners in seen.items():
            if len(owners) > 1:
                errors.append(f"duplicate {field} on {', '.join(sorted(owners))}: {value!r}")

    alts: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))
    for entry in final:
        if entry.get("resource") == "product_media" and entry.get("field") == "alt":
            alts[entry.get("handle", "")][_value(entry).strip().lower()].append(entry.get("file", "?"))
    for handle, by_value in alts.items():
        for value, files in by_value.items():
            if len(files) > 1:
                errors.append(f"duplicate alt within product {handle} on {', '.join(sorted(files))}: {value!r}")
    return errors


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    path = Path(args[0]) if args else Path(__file__).resolve().parent.parent / "seo" / "plan.json"
    plan = json.loads(path.read_text(encoding="utf-8"))
    errors = check_plan(plan)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    for error in errors:
        print(error)
    if errors:
        print(f"FAILED: {len(errors)} error(s) in {path}")
        return 1
    print(f"OK: {len(plan.get('changes') or [])} changes and {len(plan.get('kept') or [])} kept values pass ({path})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
