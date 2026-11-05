"""Extract the owner-written copy from a pulled Shopify theme's JSON templates.

The theme itself is Shopify-licensed reference material; what we keep is the merchant's own
content held in it: section and block order, text, headings, labels, links and image picks.
Styling values (colours, fonts, spacing, CSS variables, Liquid expressions) are dropped.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_LEADING_COMMENT = re.compile(r"\A\s*/\*.*?\*/", flags=re.DOTALL)
_COLOUR = re.compile(r"^\s*(#[0-9a-fA-F]{3,8}|(rgb|rgba|hsl|hsla)\(.*\))\s*$")
_LINK_PREFIXES = ("shopify://", "http://", "https://", "mailto:", "tel:", "/")
_CONTENT_KEY = re.compile(
    r"^(text|heading|subheading|title|label|caption|description|content|quote|author|placeholder"
    r"|link|url|menu|customer_account_menu|collection|collection_list|collections|product"
    r"|product_list|products|page|blog|article|custom_liquid|liquid|html|image|background_image"
    r"|video|video_url|logo|logo_inverse|favicon|button_label|button_link)(_\d+)?(_mobile)?$"
    # Not `_link$`: Horizon's `type_case_primary_link` / `type_font_primary_link` are styling.
    r"|_url$|_label$"
)


def strip_comment_header(text: str) -> str:
    """Remove the ``/* ... */`` header(s) Shopify writes above auto-generated JSON files."""
    stripped = text
    while True:
        match = _LEADING_COMMENT.match(stripped)
        if not match:
            return stripped
        stripped = stripped[match.end():]


def load_theme_json(path: Path) -> Any:
    return json.loads(strip_comment_header(path.read_text(encoding="utf-8")))


def is_content_setting(key: str, value: Any) -> bool:
    """True for owner-written content (copy, links, image picks); False for styling or empty."""
    if value is None or value == "" or value == [] or value == {}:
        return False
    if isinstance(value, (bool, int, float, dict)):
        return False
    if isinstance(value, list):
        return bool(_CONTENT_KEY.search(key)) and all(isinstance(v, str) for v in value)
    if not isinstance(value, str):
        return False
    if "{{" in value or "{%" in value or value.lstrip().startswith("var(") or _COLOUR.match(value):
        return False
    if value.startswith(_LINK_PREFIXES):
        return True
    return bool(_CONTENT_KEY.search(key))


def _content_settings(settings: Any) -> dict:
    if not isinstance(settings, dict):
        return {}
    return {k: v for k, v in settings.items() if is_content_setting(k, v)}


def _ordered_ids(children: dict, order: Any) -> list[tuple[str, bool]]:
    """``(id, in_order)`` pairs: ids from ``order`` first, the rest after in file order."""
    listed = [i for i in (order or []) if i in children]
    rest = [i for i in children if i not in listed]
    return [(i, True) for i in listed] + [(i, False) for i in rest]


def _node(node_id: str, node: dict) -> dict:
    out: dict[str, Any] = {"id": node_id, "type": node.get("type"),
                           "disabled": bool(node.get("disabled", False))}
    name = node.get("name")
    if isinstance(name, str) and name and not name.startswith("t:"):
        out["name"] = name
    if node.get("static"):
        out["static"] = True
    out["settings"] = _content_settings(node.get("settings"))
    blocks = node.get("blocks") or {}
    out["blocks"] = [_node(bid, blocks[bid]) for bid, _ in _ordered_ids(blocks, node.get("block_order"))]
    return out


def extract_sections(doc: dict) -> list[dict]:
    """Ordered sections of a template or section group, each with its ordered block tree."""
    sections = doc.get("sections") or {}
    result = []
    for section_id, in_order in _ordered_ids(sections, doc.get("order")):
        entry = _node(section_id, sections[section_id])
        if not in_order:
            entry["in_order"] = False
        result.append(entry)
    return result


def extract_theme_content(theme_dir: Path) -> dict:
    """Content of every JSON template, every section group and the current global settings."""
    theme_dir = Path(theme_dir)
    templates = {}
    for path in sorted((theme_dir / "templates").rglob("*.json")):
        doc = load_theme_json(path)
        entry: dict[str, Any] = {"sections": extract_sections(doc)}
        if doc.get("layout") is not None:
            entry["layout"] = doc["layout"]
        templates[path.relative_to(theme_dir).as_posix()] = entry

    groups = {}
    for path in sorted((theme_dir / "sections").glob("*.json")):
        doc = load_theme_json(path)
        groups[path.relative_to(theme_dir).as_posix()] = {
            "type": doc.get("type"),
            "sections": extract_sections(doc),
        }

    global_settings: dict = {}
    settings_path = theme_dir / "config" / "settings_data.json"
    if settings_path.is_file():
        current = load_theme_json(settings_path).get("current")
        global_settings = _content_settings(current)

    return {"templates": templates, "section_groups": groups, "global_settings": global_settings}
