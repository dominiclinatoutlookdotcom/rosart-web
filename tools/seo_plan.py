"""Builds the SEO and AI-search fix plan for the Rosart store, from the export and a live snapshot.

Reads only local files: `export/` (the content export), `seo/live-snapshot.json` (a read-only
store query: scopes, categories, collection image ids, theme file alts) and the authored values
in `tools/seo_content.py`. Writes `seo/plan.json` (one entry per change, deterministic order) and
`seo/plan.md` (the owner's review copy). It never calls Shopify and never writes to the store:
applying the plan is a separate, owner-approved step.

Usage: python tools/seo_plan.py   (then: python tools/seo_check.py seo/plan.json)
"""
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
RESOURCE_ORDER = ["shop", "page", "collection", "product", "product_media", "file"]

MECHANISMS: dict[tuple[str, str], tuple[str, str | None]] = {
    ("shop", "seo.title"): ("manual: Shopify admin > Online Store > Preferences > Homepage title "
                            "(no Admin API mutation; Shop.description is read-only)", None),
    ("shop", "seo.description"): ("manual: Shopify admin > Online Store > Preferences > Homepage meta "
                                  "description (Shop.description is read-only in the Admin API)", None),
    ("page", "seo.title"): ("pageUpdate(id, page: {metafields: [{namespace: \"global\", key: \"title_tag\", "
                            "type: \"single_line_text_field\", value}]})", "write_content"),
    ("page", "seo.description"): ("pageUpdate(id, page: {metafields: [{namespace: \"global\", key: "
                                  "\"description_tag\", type: \"single_line_text_field\", value}]})", "write_content"),
    ("collection", "descriptionHtml"): ("collectionUpdate(collection: {id, descriptionHtml})", "write_products"),
    ("collection", "seo.title"): ("collectionUpdate(collection: {id, seo: {title}})", "write_products"),
    ("collection", "seo.description"): ("collectionUpdate(collection: {id, seo: {description}})", "write_products"),
    ("collection", "image.altText"): ("collectionUpdate(collection: {id, image: {id, altText}})", "write_products"),
    ("product", "seo.title"): ("productUpdate(product: {id, seo: {title}})", "write_products"),
    ("product", "seo.description"): ("productUpdate(product: {id, seo: {description}})", "write_products"),
    ("product", "category"): ("productUpdate(product: {id, category})", "write_products"),
    ("product", "vendor"): ("productUpdate(product: {id, vendor})", "write_products"),
    ("product", "productType"): ("productUpdate(product: {id, productType})", "write_products"),
    ("product_media", "alt"): ("fileUpdate(files: [{id, alt}])", "write_files"),
    ("file", "alt"): ("fileUpdate(files: [{id, alt}])", "write_files"),
}


# ---------------------------------------------------------------- reading the export

def _read(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_export(export_dir: Path | str) -> dict[str, Any]:
    """The parts of the content export the plan needs, read as UTF-8."""
    base = Path(export_dir)
    shop = base / "shopify"
    return {
        "products": _read(shop / "products.json"),
        "collections": _read(shop / "collections.json"),
        "pages": _read(shop / "pages.json"),
        "shop": _read(shop / "shop.json"),
        "files": _read(shop / "files.json"),
        "sources": _read(base / "images" / "manifest.json").get("sources", {}),
        "theme": _read(base / "theme-content.json"),
    }


def html_text(value: str | None) -> str:
    """Plain text of owner HTML: tags and style blocks dropped, entities decoded, spaces collapsed."""
    value = re.sub(r"<style.*?</style>", " ", value or "", flags=re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", value)).split())


def metafield_text(field: dict[str, Any]) -> str:
    """Readable text of a metafield value; rich text JSON is flattened to its text nodes."""
    value = field.get("value") or ""
    if field.get("type") == "rich_text_field":
        parts: list[str] = []

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                if isinstance(node.get("value"), str):
                    parts.append(node["value"])
                for child in node.get("children", []):
                    walk(child)

        try:
            walk(json.loads(value))
        except json.JSONDecodeError:
            return " ".join(value.split())
        return " ".join(" ".join(parts).split())
    return " ".join(value.split())


def theme_texts(node: Any) -> list[str]:
    """Owner-written text in a theme template or section group, in document order."""
    found: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            if key in ("text", "heading", "label", "custom_liquid") and isinstance(value, str):
                if key == "custom_liquid":
                    alts = re.findall(r'alt="([^"]+)"', value)
                    text = html_text(value) + (" Image alt: " + "; ".join(alts) if alts else "")
                else:
                    text = html_text(value)
                if text:
                    found.append(text)
            else:
                found.extend(theme_texts(value))
    elif isinstance(node, list):
        for item in node:
            found.extend(theme_texts(item))
    return found


def local_file(url: str, sources: dict[str, str]) -> str:
    canonical = url.split("?")[0]
    return sources.get(canonical, canonical.rsplit("/", 1)[-1])


# ---------------------------------------------------------------- building entries

def _src(ref: str, text: str) -> dict[str, str]:
    return {"ref": ref, "text": text}


def _product_sources(p: dict[str, Any], exclude_metafields: bool) -> list[dict[str, str]]:
    sources = [_src("product.title", p["title"]), _src("product.descriptionHtml", html_text(p["descriptionHtml"]))]
    if not exclude_metafields:
        for field in sorted(p.get("metafields", []), key=lambda f: (f["namespace"], f["key"])):
            if field["namespace"] == "custom" and field["key"] != "orizen_url":
                sources.append(_src(f"metafield.custom.{field['key']}", metafield_text(field)))
    return sources


def _entry(resource: str, handle: str, field: str, gid: str, before: Any, after: Any,
           sources: list[dict[str, str]], *, file: str | None = None, position: int | None = None,
           **extra: Any) -> dict[str, Any]:
    mechanism, scope = MECHANISMS[(resource, field)]
    key = f"{resource}/{handle}/{field}"
    if file is not None:
        key += f"/{position:02d}-{file}" if position is not None else f"/{file}"
    entry = {"id": key, "resource": resource, "gid": gid, "handle": handle, "field": field,
             "before": before, "after": after, "sources": sources,
             "mechanism": mechanism, "scope": scope}
    if file is not None:
        entry["file"] = file
    entry.update(extra)
    return entry


def _maybe(changes: list, kept: list, entry: dict[str, Any]) -> None:
    """A change when the value differs; otherwise a kept value (still validated by the checker)."""
    if entry["after"] is None:
        return
    if entry["before"] == entry["after"]:
        kept.append(_as_kept(entry))
    else:
        changes.append(entry)


def _as_kept(entry: dict[str, Any]) -> dict[str, Any]:
    kept = {k: v for k, v in entry.items() if k not in ("before", "after", "mechanism", "scope")}
    kept["value"] = entry["before"]
    return kept


def build_plan(export: dict[str, Any], snapshot: dict[str, Any], content: dict[str, Any]) -> dict[str, Any]:
    changes: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    sources_map = export["sources"]
    theme = export["theme"]
    templates = theme.get("templates", {})
    footer = [t for group in theme.get("section_groups", {}).values() for t in theme_texts(group)]
    products = {p["handle"]: p for p in export["products"] if p.get("status") == "ACTIVE"}
    product_content = content.get("products", {})

    # Shop (homepage): set by hand in the admin, there is no Admin API write for it.
    shop_snap = snapshot.get("shop", {})
    shop_sources = ([_src("shop.name", export["shop"].get("name", ""))]
                    + [_src("theme.templates/index.json", t) for t in theme_texts(templates.get("templates/index.json", {}))]
                    + [_src("theme.section_groups", t) for t in footer])
    shop_content = content.get("shop", {})
    shop_before = {"seo.title": None, "seo.description": shop_snap.get("description") or ""}
    for field in ("seo.title", "seo.description"):
        if field in shop_content:
            _maybe(changes, kept, _entry("shop", "home", field, shop_snap.get("id", ""), shop_before[field],
                                         shop_content[field], shop_sources,
                                         note="Before: the homepage title is not readable through the Admin API"
                                         if field == "seo.title" else "Before: Shop.description"))

    # Pages: SEO lives in the global.title_tag / description_tag metafields.
    collection_titles = [c["title"] for c in export["collections"] if c["handle"] != "frontpage"]
    for page in export["pages"]:
        handle = page["handle"]
        page_content = content.get("pages", {}).get(handle)
        if not page_content:
            continue
        snap = snapshot.get("pages", {}).get(handle, {})
        page_sources = ([_src("shop.name", export["shop"].get("name", "")), _src("page.title", page["title"]),
                         _src("page.bodyHtml", html_text(page.get("bodyHtml")))]
                        + [_src(f"theme.templates/page.{handle}.json", t)
                           for t in theme_texts(templates.get(f"templates/page.{handle}.json", {}))]
                        + [_src("theme.section_groups", t) for t in footer])
        if handle == "our-collection":
            page_sources.append(_src("collections.title", ", ".join(collection_titles)))
            page_sources.append(_src("products.title", ", ".join(sorted(p["title"] for p in products.values()))))
        befores = {"seo.title": snap.get("title_tag") or page["seo"].get("title") or "",
                   "seo.description": snap.get("description_tag") or page["seo"].get("description") or ""}
        for field in ("seo.title", "seo.description"):
            if field in page_content:
                _maybe(changes, kept, _entry("page", handle, field, page["id"], befores[field],
                                             page_content[field], page_sources))

    # Collections: description and SEO from the products they hold.
    for collection in export["collections"]:
        handle = collection["handle"]
        coll_content = content.get("collections", {}).get(handle)
        if not coll_content:
            continue
        members = [products[h] for h in collection["productHandles"] if h in products]
        coll_sources = [_src("collection.title", collection["title"])]
        for member in members:
            exclude = product_content.get(member["handle"], {}).get("exclude_metafields", False)
            coll_sources.extend(_src(f"{s['ref']} ({member['handle']})", s["text"])
                                for s in _product_sources(member, exclude))
        befores = {"descriptionHtml": collection.get("descriptionHtml") or "",
                   "seo.title": collection["seo"].get("title") or "",
                   "seo.description": collection["seo"].get("description") or ""}
        for field in ("descriptionHtml", "seo.title", "seo.description"):
            if field in coll_content:
                _maybe(changes, kept, _entry("collection", handle, field, collection["id"], befores[field],
                                             coll_content[field], coll_sources))
        if "image.altText" in coll_content:
            image = snapshot.get("collections", {}).get(handle, {}).get("image") or {}
            observed = content.get("collection_image_observations", {}).get(handle)
            image_sources = coll_sources + ([_src("image (viewed 2026-11-07)", observed)] if observed else [])
            _maybe(changes, kept, _entry("collection", handle, "image.altText", image.get("id", ""),
                                         image.get("altText") or "", coll_content["image.altText"], image_sources))

    # Products: SEO, taxonomy category, other listing fields, and alt text per image.
    category_labels = content.get("categories", {})
    observations = content.get("image_observations", {})
    for handle in sorted(products):
        p = products[handle]
        pc = product_content.get(handle, {})
        sources = _product_sources(p, pc.get("exclude_metafields", False))
        for field in ("seo.title", "seo.description"):
            before = p["seo"].get(field.split(".")[1]) or ""
            after = pc.get(field, before or None)
            _maybe(changes, kept, _entry("product", handle, field, p["id"], before, after, sources))
        for field in ("vendor", "productType"):
            if field in pc:
                _maybe(changes, kept, _entry("product", handle, field, p["id"], p.get(field) or "", pc[field], sources))
        if "category" in pc:
            current = (snapshot.get("products", {}).get(handle, {}) or {}).get("category") or {}
            entry = _entry("product", handle, "category", p["id"], current.get("id"), pc["category"],
                           [_src("product.productType", p.get("productType") or "")] + sources,
                           before_label=current.get("fullName"), after_label=category_labels.get(pc["category"]))
            if entry["before"] != entry["after"]:
                changes.append(entry)
        alt_views = pc.get("alt", {})
        for position, media in enumerate(p.get("media", []), start=1):
            image = media.get("image") or {}
            file = local_file(image.get("url", ""), sources_map)
            if file not in alt_views:
                continue
            alt_sources = [_src("product.title", p["title"])]
            if file in observations:
                alt_sources.append(_src(f"image {file} (viewed 2026-11-07)", observations[file]))
            _maybe(changes, kept, _entry("product_media", handle, "alt", media["id"], media.get("alt") or "",
                                         f"{p['title']}, {alt_views[file]}", alt_sources,
                                         file=file, position=position))

    # Theme images in Shopify Files: alt where the image carries meaning, decorative otherwise.
    files_by_name = {local_file(f["url"], sources_map): f for f in export["files"]}
    for name, spec in sorted(content.get("files", {}).items()):
        f = files_by_name[name]
        file_sources = [_src(f"image {name} (viewed 2026-11-07)", spec["observed"]), _src("site use", spec["use"])]
        _maybe(changes, kept, _entry("file", name, "alt", f["id"], f.get("alt") or "", spec["alt"],
                                     file_sources, file=name))
    decorative = [{"file": name, "gid": files_by_name[name]["id"], "reason": reason}
                  for name, reason in sorted(content.get("decorative", {}).items())]

    changes.sort(key=lambda c: (RESOURCE_ORDER.index(c["resource"]), c["id"]))
    kept.sort(key=lambda c: (RESOURCE_ORDER.index(c["resource"]), c["id"]))
    current = sorted(snapshot.get("access_scopes", []))
    required = sorted({c["scope"] for c in changes if c["scope"]})
    skus = sorted({v["sku"] for p in products.values() for v in p.get("variants", []) if v.get("sku")})
    return {
        "store": snapshot.get("store", ""),
        "generated_from": {"export": "export/shopify/*.json, export/theme-content.json, export/images/manifest.json",
                           "snapshot": "seo/live-snapshot.json", "content": "tools/seo_content.py"},
        "skus": skus,
        "scopes": {"current": current, "required": required,
                   "missing": [s for s in required if s not in current]},
        "changes": changes,
        "kept": kept,
        "decorative": decorative,
        "findings": content.get("findings", []),
        "references": content.get("references", []),
    }


# ---------------------------------------------------------------- the owner's review copy

def _cell(value: Any) -> str:
    if value is None:
        return "*(not readable via API)*"
    text = str(value)
    return "*(empty)*" if text == "" else text.replace("|", "\\|").replace("\n", " ")


def _count(entry: dict[str, Any]) -> str:
    before = entry["before"]
    return f"({len(before) if isinstance(before, str) else '?'} → {len(str(entry['after']))})"


def render_markdown(plan: dict[str, Any]) -> str:
    changes = plan["changes"]
    lines = ["# Rosart SEO and AI-search fix plan (dry run, not applied)", "",
             f"Store `{plan['store']}`. Built by `tools/seo_plan.py` from the export and a read-only "
             "store snapshot; validated by `tools/seo_check.py`. Nothing here has been written to the store.",
             "Approve this file (or strike rows) before any apply step. Character counts are (before → after).", ""]

    counts: dict[tuple[str, str], int] = {}
    for c in changes:
        counts[(c["resource"], c["field"])] = counts.get((c["resource"], c["field"]), 0) + 1
    lines += ["## Summary", "", "| Resource | Field | Changes |", "| --- | --- | --- |"]
    lines += [f"| {r} | {f} | {n} |" for (r, f), n in sorted(counts.items(), key=lambda i: (RESOURCE_ORDER.index(i[0][0]), i[0][1]))]
    lines += [f"| **total** | | **{len(changes)}** |", "",
              f"Kept unchanged (already pass the checker): {len(plan['kept'])} values. "
              f"Decorative images (empty alt is correct, no write): {len(plan['decorative'])}.", ""]

    scopes = plan["scopes"]
    lines += ["## Access scopes", "",
              f"- Current (read 2026-11-07): `{', '.join(scopes['current'])}`",
              f"- Needed by the apply step: `{', '.join(scopes['required'])}`",
              f"- **Missing: `{', '.join(scopes['missing']) or 'none'}`**", "",
              "| Mechanism | Scope | Used for |", "| --- | --- | --- |"]
    by_mechanism: dict[tuple[str, str], set[str]] = {}
    for c in changes:
        key = (c["mechanism"].split("(")[0], c["scope"] or "none (admin UI)")
        by_mechanism.setdefault(key, set()).add(f"{c['resource']} {c['field']}")
    lines += [f"| `{m}` | `{s}` | {', '.join(sorted(u))} |" for (m, s), u in sorted(by_mechanism.items())]
    lines += [""]

    titles = {"shop": "Homepage (shop)", "page": "Pages", "collection": "Collections", "product": "Products",
              "product_media": "Product images (alt text)", "file": "Theme images in Shopify Files (alt text)"}
    for resource in RESOURCE_ORDER:
        group = [c for c in changes if c["resource"] == resource]
        if not group:
            continue
        lines += [f"## {titles[resource]}", ""]
        handles = sorted({c["handle"] for c in group}, key=lambda h: [c["id"] for c in group if c["handle"] == h][0])
        for handle in handles:
            rows = [c for c in group if c["handle"] == handle]
            lines += [f"### {handle}", "", f"`{rows[0]['gid']}`" if resource != "product_media" else "", ""]
            if resource in ("product_media", "file"):
                lines += ["| File | Media gid | Before | After | Chars |", "| --- | --- | --- | --- | --- |"]
                lines += [f"| {_cell(c['file'])} | `{c['gid']}` | {_cell(c['before'])} | {_cell(c['after'])} | {_count(c)} |"
                          for c in rows]
            else:
                lines += ["| Field | Before | After | Chars |", "| --- | --- | --- | --- |"]
                for c in rows:
                    before, after = c["before"], c["after"]
                    if c["field"] == "category":
                        before, after = f"{c.get('before_label')} (`{before}`)", f"{c.get('after_label')} (`{after}`)"
                        lines.append(f"| {c['field']} | {_cell(before)} | {_cell(after)} | |")
                    else:
                        lines.append(f"| {c['field']} | {_cell(before)} | {_cell(after)} | {_count(c)} |")
            notes = [c["note"] for c in rows if c.get("note")]
            lines += [f"Note: {n}" for n in notes] + [""]

    lines += ["## Decorative images (no write: empty alt is correct)", "", "| File | gid | Why |", "| --- | --- | --- |"]
    lines += [f"| {_cell(d['file'])} | `{d['gid']}` | {_cell(d['reason'])} |" for d in plan["decorative"]]
    lines += ["", "## Kept values (no change; validated)", "", "| Resource | Handle | Field | Value | Chars |",
              "| --- | --- | --- | --- | --- |"]
    lines += [f"| {k['resource']} | {k['handle']} | {k['field']} | {_cell(k['value'])} | {len(k['value'])} |"
              for k in plan["kept"]]
    if plan.get("findings"):
        lines += ["", "## Store-level findings (report only: owner decisions, not in this plan)", ""]
        for i, f in enumerate(plan["findings"], start=1):
            lines += [f"{i}. **{f['topic']}**: {f['finding']}"]
            if f.get("evidence"):
                lines += [f"   Evidence: {f['evidence']}"]
            if f.get("decision"):
                lines += [f"   Decision needed: {f['decision']}"]
    if plan.get("references"):
        lines += ["", "## API references (shopify.dev, Admin GraphQL 2026-10)", ""]
        lines += [f"- {r}" for r in plan["references"]]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    from seo_content import CONTENT

    export = load_export(ROOT / "export")
    snapshot = _read(ROOT / "seo" / "live-snapshot.json")
    plan = build_plan(export, snapshot, CONTENT)
    out = ROOT / "seo"
    out.mkdir(exist_ok=True)
    (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    (out / "plan.md").write_text(render_markdown(plan), encoding="utf-8", newline="\n")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(f"Wrote {out / 'plan.json'} ({len(plan['changes'])} changes, {len(plan['kept'])} kept) and {out / 'plan.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
