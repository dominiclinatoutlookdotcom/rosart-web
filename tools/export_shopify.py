"""Read-only content export of the Rosart Shopify store.

Writes pretty, stably sorted JSON to ``export/shopify/``, the theme's owner-written copy to
``export/theme-content.json`` and every referenced image to ``export/images/`` with a manifest.
Re-runnable: images already on disk with the recorded sha256 and size are not fetched again.

Usage::

    python tools/export_shopify.py [--store DOMAIN] [--export-dir DIR] [--theme-dir DIR] [--skip-images]
                                   [--transport cli|admin-api] [--theme ID]

``--transport cli`` (default) uses the signed-in Shopify CLI; the theme is pulled beforehand
(``make theme-pull``). ``--transport admin-api`` talks to the Admin API directly with the
``ROSART_SHOPIFY_*`` keys of the environment (client credentials by default), reads the live
theme into the theme folder first, and needs no browser sign-in.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Callable, Mapping

from downloads import Fetch, Sleep, download_images, make_urllib_fetch, real_sleep
from images import (
    assign_local_names,
    canonical_url,
    filename_from_url,
    find_cdn_image_urls,
    find_shop_image_refs,
    resolve_shop_images,
)
from pull_theme import DEFAULT_THEME
from shopify_admin import AdminApiRunner, TokenConfigError, default_opener, token_source_from_config
from shopify_cli import Runner, ShopifyCliError, ShopifyCliRunner, complete_connection, paginate
from theme_admin import read_theme
from theme_content import extract_theme_content, load_theme_json

DEFAULT_STORE = "rosart-mhfmyuxq.myshopify.com"
REPO_ROOT = Path(__file__).resolve().parent.parent
ADMIN_DEADLINE_S = 2 * 3600   # the whole Admin API export, on the monotonic clock

PAGE_INFO = "pageInfo { hasNextPage endCursor }"
SEO_FIELDS = "seo { title description }"
IMAGE_FIELDS = "url altText width height"
VARIANT_FIELDS = """id title sku price compareAtPrice barcode position taxable
  selectedOptions { name value }"""
MEDIA_FIELDS = f"""id mediaContentType alt
  ... on MediaImage {{ image {{ {IMAGE_FIELDS} }} }}
  ... on Video {{ sources {{ url mimeType width height }} }}
  ... on ExternalVideo {{ originUrl }}"""
METAFIELD_FIELDS = "namespace key type value"
MENU_ITEM_FIELDS = "id title type url resourceId tags"

QUERY_SHOP = """query Shop {
  shop { name description myshopifyDomain url currencyCode ianaTimezone primaryDomain { url host } }
}"""

QUERY_PAGES = f"""query Pages($after: String) {{
  pages(first: 100, after: $after) {{
    nodes {{ id title handle body isPublished publishedAt templateSuffix createdAt updatedAt
      titleTag: metafield(namespace: "global", key: "title_tag") {{ value }}
      descriptionTag: metafield(namespace: "global", key: "description_tag") {{ value }} }}
    {PAGE_INFO}
  }}
}}"""

QUERY_PRODUCTS = f"""query Products($after: String) {{
  products(first: 50, after: $after) {{
    nodes {{ id title handle descriptionHtml productType vendor tags status templateSuffix
      createdAt updatedAt publishedAt {SEO_FIELDS}
      options {{ id name position values }}
      variants(first: 100) {{ nodes {{ {VARIANT_FIELDS} }} {PAGE_INFO} }}
      media(first: 100) {{ nodes {{ {MEDIA_FIELDS} }} {PAGE_INFO} }}
      metafields(first: 100) {{ nodes {{ {METAFIELD_FIELDS} }} {PAGE_INFO} }} }}
    {PAGE_INFO}
  }}
}}"""


def _nested_product_query(name: str, field: str, fields: str) -> str:
    return f"""query {name}($id: ID!, $after: String) {{
  node(id: $id) {{ ... on Product {{ {field}(first: 250, after: $after) {{ nodes {{ {fields} }} {PAGE_INFO} }} }} }}
}}"""


QUERY_PRODUCT_VARIANTS = _nested_product_query("ProductVariants", "variants", VARIANT_FIELDS)
QUERY_PRODUCT_MEDIA = _nested_product_query("ProductMedia", "media", MEDIA_FIELDS)
QUERY_PRODUCT_METAFIELDS = _nested_product_query("ProductMetafields", "metafields", METAFIELD_FIELDS)

QUERY_COLLECTIONS = f"""query Collections($after: String) {{
  collections(first: 50, after: $after) {{
    nodes {{ id title handle descriptionHtml templateSuffix sortOrder updatedAt
      image {{ {IMAGE_FIELDS} }} {SEO_FIELDS}
      ruleSet {{ appliedDisjunctively rules {{ column relation condition }} }}
      products(first: 250) {{ nodes {{ handle }} {PAGE_INFO} }} }}
    {PAGE_INFO}
  }}
}}"""

QUERY_COLLECTION_PRODUCTS = f"""query CollectionProducts($id: ID!, $after: String) {{
  node(id: $id) {{ ... on Collection {{ products(first: 250, after: $after) {{ nodes {{ handle }} {PAGE_INFO} }} }} }}
}}"""

QUERY_MENUS = f"""query Menus($after: String) {{
  menus(first: 50, after: $after) {{
    nodes {{ id handle title
      items {{ {MENU_ITEM_FIELDS} items {{ {MENU_ITEM_FIELDS} items {{ {MENU_ITEM_FIELDS} }} }} }} }}
    {PAGE_INFO}
  }}
}}"""

QUERY_BLOGS = f"""query Blogs($after: String) {{
  blogs(first: 50, after: $after) {{
    nodes {{ id title handle templateSuffix commentPolicy createdAt updatedAt }}
    {PAGE_INFO}
  }}
}}"""

QUERY_ARTICLES = f"""query Articles($after: String) {{
  articles(first: 100, after: $after) {{
    nodes {{ id title handle body summary tags publishedAt isPublished templateSuffix
      author {{ name }} blog {{ handle }} image {{ {IMAGE_FIELDS} }} }}
    {PAGE_INFO}
  }}
}}"""

QUERY_FILES = f"""query Files($after: String) {{
  files(first: 100, after: $after) {{
    nodes {{ id alt createdAt fileStatus __typename
      ... on MediaImage {{ mimeType image {{ {IMAGE_FIELDS} }} originalSource {{ fileSize }} }}
      ... on GenericFile {{ url mimeType originalFileSize }}
      ... on Video {{ filename sources {{ url mimeType width height }} originalSource {{ url }} }} }}
    {PAGE_INFO}
  }}
}}"""


# --- output -------------------------------------------------------------------------------

def write_json(path: Path, data: Any) -> None:
    """Pretty JSON with sorted keys and a trailing newline, so re-runs diff cleanly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8", newline="\n")


def _by_handle(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda item: (item.get("handle") or "", item.get("id") or ""))


# --- fetch and shape ----------------------------------------------------------------------

def fetch_pages(run: Runner) -> list[dict]:
    pages = []
    for node in paginate(run, QUERY_PAGES, ["pages"]):
        page = {k: v for k, v in node.items() if k not in ("body", "titleTag", "descriptionTag")}
        page["bodyHtml"] = node.get("body")
        page["seo"] = {"title": (node.get("titleTag") or {}).get("value"),
                       "description": (node.get("descriptionTag") or {}).get("value")}
        pages.append(page)
    return _by_handle(pages)


def fetch_products(run: Runner) -> list[dict]:
    products = []
    for node in paginate(run, QUERY_PRODUCTS, ["products"]):
        product = dict(node)
        product["variants"] = sorted(
            complete_connection(run, node["variants"], QUERY_PRODUCT_VARIANTS, ["node", "variants"], node["id"]),
            key=lambda v: (v.get("position") or 0, v.get("id") or ""))
        product["media"] = complete_connection(run, node["media"], QUERY_PRODUCT_MEDIA, ["node", "media"], node["id"])
        product["images"] = [m["image"] for m in product["media"] if m.get("image")]
        product["metafields"] = sorted(
            complete_connection(run, node["metafields"], QUERY_PRODUCT_METAFIELDS, ["node", "metafields"], node["id"]),
            key=lambda m: (m.get("namespace") or "", m.get("key") or ""))
        products.append(product)
    return _by_handle(products)


def fetch_collections(run: Runner) -> list[dict]:
    collections = []
    for node in paginate(run, QUERY_COLLECTIONS, ["collections"]):
        collection = {k: v for k, v in node.items() if k != "products"}
        products = complete_connection(run, node["products"], QUERY_COLLECTION_PRODUCTS,
                                       ["node", "products"], node["id"])
        collection["productHandles"] = [p["handle"] for p in products]
        collections.append(collection)
    return _by_handle(collections)


def annotate_menu_items(items: list[dict], handle_by_id: dict[str, str]) -> list[dict]:
    """Add ``resourceHandle`` (the page/product/collection/blog handle) to each item, recursively."""
    annotated = []
    for item in items or []:
        entry = {k: v for k, v in item.items() if k != "items"}
        entry["resourceHandle"] = handle_by_id.get(item.get("resourceId") or "")
        entry["items"] = annotate_menu_items(item.get("items") or [], handle_by_id)
        annotated.append(entry)
    return annotated


def count_menu_items(items: list[dict]) -> int:
    return sum(1 + count_menu_items(item.get("items") or []) for item in items)


def fetch_menus(run: Runner, handle_by_id: dict[str, str]) -> list[dict]:
    menus = []
    for node in paginate(run, QUERY_MENUS, ["menus"]):
        menu = dict(node)
        menu["items"] = annotate_menu_items(node.get("items") or [], handle_by_id)
        menus.append(menu)
    return _by_handle(menus)


def fetch_blogs(run: Runner) -> list[dict]:
    articles_by_blog: dict[str, list[dict]] = {}
    for node in paginate(run, QUERY_ARTICLES, ["articles"]):
        article = {k: v for k, v in node.items() if k not in ("body", "blog")}
        article["bodyHtml"] = node.get("body")
        articles_by_blog.setdefault((node.get("blog") or {}).get("handle") or "", []).append(article)
    blogs = []
    for node in paginate(run, QUERY_BLOGS, ["blogs"]):
        blog = dict(node)
        blog["articles"] = _by_handle(articles_by_blog.get(node.get("handle") or "", []))
        blogs.append(blog)
    return _by_handle(blogs)


def shape_file(node: dict) -> dict:
    """One Files-API node as ``{id, type, alt, url, filename, width, height, mime, bytes, ...}``."""
    kind = node.get("__typename")
    image = node.get("image") or {}
    sources = node.get("sources") or []
    url = image.get("url") or node.get("url") or (sources[0].get("url") if sources else None)
    filename = node.get("filename") or (filename_from_url(url) if url else None)
    return {
        "id": node.get("id"),
        "type": kind,
        "alt": node.get("alt"),
        "url": url,
        "filename": filename,
        "width": image.get("width") or (sources[0].get("width") if sources else None),
        "height": image.get("height") or (sources[0].get("height") if sources else None),
        "mime": node.get("mimeType") or (sources[0].get("mimeType") if sources else None),
        "bytes": (node.get("originalSource") or {}).get("fileSize") or node.get("originalFileSize"),
        "fileStatus": node.get("fileStatus"),
        "createdAt": node.get("createdAt"),
    }


def fetch_files(run: Runner) -> list[dict]:
    files = [shape_file(node) for node in paginate(run, QUERY_FILES, ["files"])]
    return sorted(files, key=lambda f: ((f["filename"] or "").lower(), f["filename"] or "", f["id"] or ""))


# --- images -------------------------------------------------------------------------------

def theme_json_files(theme_dir: Path) -> list[Path]:
    paths = sorted((theme_dir / "templates").rglob("*.json")) + sorted((theme_dir / "sections").glob("*.json"))
    settings = theme_dir / "config" / "settings_data.json"
    return paths + ([settings] if settings.is_file() else [])


class ImageReferences:
    """Collects image URLs (grouped by canonical URL) and who references them."""

    def __init__(self) -> None:
        self.urls: dict[str, set[str]] = {}
        self.refs: dict[str, set[str]] = {}

    def add(self, url: str, reference: str) -> None:
        key = canonical_url(url)
        self.urls.setdefault(key, set()).add(url)
        self.refs.setdefault(key, set()).add(reference)

    def add_html(self, value: Any, reference: str) -> None:
        for url in find_cdn_image_urls(value):
            self.add(url, reference)


def collect_image_references(pages: list[dict], products: list[dict], collections: list[dict],
                             blogs: list[dict], files: list[dict], theme_dir: Path):
    """Return ``(references, shop_images resolved name->url, unresolved name->[theme files])``."""
    refs = ImageReferences()
    for product in products:
        label = f"product:{product['handle']}"
        for image in product.get("images", []):
            refs.add(image["url"], label)
        refs.add_html([product.get("descriptionHtml"), [m.get("value") for m in product.get("metafields", [])]], label)
    for collection in collections:
        label = f"collection:{collection['handle']}"
        if (collection.get("image") or {}).get("url"):
            refs.add(collection["image"]["url"], label)
        refs.add_html(collection.get("descriptionHtml"), label)
    for page in pages:
        refs.add_html(page.get("bodyHtml"), f"page:{page['handle']}")
    for blog in blogs:
        for article in blog.get("articles", []):
            label = f"article:{blog['handle']}/{article['handle']}"
            if (article.get("image") or {}).get("url"):
                refs.add(article["image"]["url"], label)
            refs.add_html(article.get("bodyHtml"), label)
    for file in files:
        if file["type"] == "MediaImage" and file.get("url"):
            refs.add(file["url"], f"file:{file['id']}")

    used_in: dict[str, set[str]] = {}
    for path in theme_json_files(theme_dir):
        doc = load_theme_json(path)
        relative = path.relative_to(theme_dir).as_posix()
        refs.add_html(doc, f"theme:{relative}")
        for name in find_shop_image_refs(doc):
            used_in.setdefault(name, set()).add(relative)

    image_files = [f for f in files if f["type"] == "MediaImage"]
    resolved, unresolved = resolve_shop_images(used_in, image_files)
    for name, url in resolved.items():
        refs.add(url, f"shop_images:{name}")
    return refs, resolved, {name: sorted(used_in[name]) for name in unresolved}


def export_images(refs: ImageReferences, resolved: dict[str, str], unresolved: dict[str, list[str]],
                  images_dir: Path, fetch: Fetch, sleep: Sleep) -> dict:
    names = assign_local_names(refs.urls)
    # One download URL per file. ``?v=`` is only a cache-buster (the CDN serves the current file
    # for any value), so with several variants any deterministic pick is correct.
    plan = {max(refs.urls[c]): names[c] for c in names}

    manifest_path = images_dir / "manifest.json"
    previous: dict = {}
    if manifest_path.is_file():
        previous = json.loads(manifest_path.read_text(encoding="utf-8")).get("images", {})

    result = download_images(plan, images_dir, fetch, sleep, previous=previous)
    images = {}
    for canonical, name in names.items():
        if name in result.records:
            images[name] = dict(result.records[name], references=sorted(refs.refs[canonical]))
    manifest = {
        "note": "Paths are filenames relative to this folder. sources: canonical CDN URL (no query) -> file.",
        "images": images,
        "sources": {canonical: name for canonical, name in sorted(names.items())},
        "shop_images": {name: names[canonical_url(url)] for name, url in sorted(resolved.items())},
        "unresolved_shop_images": unresolved,
        "failed": result.failed,
    }
    write_json(manifest_path, manifest)

    planned = {name.lower() for name in names.values()} | {"manifest.json"}
    stale = sorted(p.name for p in images_dir.iterdir() if p.is_file() and p.name.lower() not in planned)
    return {
        "referenced": len(names),
        "downloaded": len(result.downloaded),
        "skipped": len(result.skipped),
        "failed": result.failed,
        "total_bytes": sum(entry["bytes"] for entry in images.values()),
        "stale_files_not_referenced": stale,
    }


# --- orchestration ------------------------------------------------------------------------

def run_export(run: Runner, fetch: Fetch, sleep: Sleep, export_dir: Path, theme_dir: Path,
               download: bool = True, log: Callable[[str], None] = lambda message: None) -> dict:
    """Export everything; return a summary of counts, image results and unresolved references."""
    export_dir, theme_dir = Path(export_dir), Path(theme_dir)
    if not (theme_dir / "templates").is_dir():
        raise SystemExit(f"no theme at {theme_dir}: run `shopify theme pull` into it first")
    shopify_dir = export_dir / "shopify"

    log("shop"); shop = run(QUERY_SHOP, {})["shop"]
    log("pages"); pages = fetch_pages(run)
    log("products"); products = fetch_products(run)
    log("collections"); collections = fetch_collections(run)
    log("blogs"); blogs = fetch_blogs(run)
    handle_by_id = {item["id"]: item["handle"] for item in pages + products + collections + blogs}
    log("menus"); menus = fetch_menus(run, handle_by_id)
    log("files"); files = fetch_files(run)

    for name, data in [("shop", shop), ("pages", pages), ("products", products),
                       ("collections", collections), ("menus", menus), ("blogs", blogs), ("files", files)]:
        write_json(shopify_dir / f"{name}.json", data)
    log("theme content")
    write_json(export_dir / "theme-content.json", extract_theme_content(theme_dir))

    refs, resolved, unresolved = collect_image_references(pages, products, collections, blogs, files, theme_dir)
    summary: dict[str, Any] = {
        "pages": len(pages), "products": len(products), "collections": len(collections),
        "menus": len(menus), "menu_items": sum(count_menu_items(m["items"]) for m in menus),
        "blogs": len(blogs), "articles": sum(len(b["articles"]) for b in blogs), "files": len(files),
        "unresolved_shop_images": unresolved,
    }
    if download:
        log(f"images ({len(refs.urls)} referenced)")
        summary["images"] = export_images(refs, resolved, unresolved, export_dir / "images", fetch, sleep)
    return summary


def main(argv: list[str] | None = None, env: Mapping[str, str] | None = None, opener: Any = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--store", default=DEFAULT_STORE)
    parser.add_argument("--export-dir", type=Path, default=REPO_ROOT / "export")
    parser.add_argument("--theme-dir", type=Path, default=None, help="default: <export-dir>/theme")
    parser.add_argument("--skip-images", action="store_true", help="write the JSON only, download nothing")
    parser.add_argument("--transport", choices=("cli", "admin-api"), default="cli",
                        help="cli: the Shopify CLI (default); admin-api: the Admin API with ROSART_SHOPIFY_* keys")
    parser.add_argument("--theme", default=DEFAULT_THEME, help="theme ID read by --transport admin-api")
    args = parser.parse_args(argv)
    log = lambda message: print(f"export: {message}", file=sys.stderr, flush=True)  # noqa: E731
    theme_dir = args.theme_dir or args.export_dir / "theme"

    if args.transport == "cli":
        summary = run_export(ShopifyCliRunner(args.store), make_urllib_fetch(), real_sleep,
                             args.export_dir, theme_dir, download=not args.skip_images, log=log)
    else:
        opener = opener or default_opener(args.store)
        fetch = make_urllib_fetch()
        try:
            tokens = token_source_from_config(os.environ if env is None else env, shop=args.store,
                                              opener=opener, clock=time.monotonic)
            runner = AdminApiRunner(args.store, tokens, opener, real_sleep, time.monotonic() + ADMIN_DEADLINE_S)
            log("theme files")
            read_theme(runner, fetch, args.theme, theme_dir)
            summary = run_export(runner, fetch, real_sleep, args.export_dir, theme_dir,
                                 download=not args.skip_images, log=log)
        except (TokenConfigError, ShopifyCliError) as exc:
            print(f"export: {exc}", file=sys.stderr)
            return 1
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 1 if summary.get("images", {}).get("failed") else 0


if __name__ == "__main__":
    sys.exit(main())
