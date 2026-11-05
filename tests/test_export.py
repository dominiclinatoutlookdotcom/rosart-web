"""End-to-end export with a fake Shopify CLI runner and a fake image fetch (no network)."""
import json
import re
import shutil
from pathlib import Path

import pytest

from export_shopify import run_export

FIXTURE_THEME = Path(__file__).parent / "fixtures" / "theme"
CDN = "https://cdn.shopify.com/s/files/1/1/files"


def connection(nodes, cursor=None):
    return {"nodes": nodes, "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor}}


PAGE_STORY = {"id": "gid://shopify/Page/2", "title": "Our Story", "handle": "our-story",
              "body": f'<p>Roses</p><img src="{CDN}/field.jpg?v=3">', "isPublished": True,
              "publishedAt": "2026-01-01T00:00:00Z", "templateSuffix": "our-story",
              "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-02T00:00:00Z",
              "titleTag": {"value": "Our Story | Rosart"}, "descriptionTag": None}
PAGE_CONTACT = dict(PAGE_STORY, id="gid://shopify/Page/1", title="Contact", handle="contact",
                    body="<p>Hi</p>", templateSuffix="contact", titleTag=None)

PRODUCT = {
    "id": "gid://shopify/Product/9", "title": "Rose Face Cream", "handle": "rose-face-cream",
    "descriptionHtml": "<p>Cream</p>", "productType": "Moisturizer", "vendor": "Rosart",
    "tags": ["Face Care"], "status": "ACTIVE", "templateSuffix": None,
    "seo": {"title": "Cream SEO", "description": "Cream description"},
    "options": [{"id": "o1", "name": "Size", "position": 1, "values": ["50ml", "3g"],
                 "optionValues": [{"name": "50ml"}, {"name": "3g"}]}],
    "variants": connection([{"id": "v1", "title": "50ml", "sku": "C50", "price": "65.00",
                             "compareAtPrice": None, "barcode": None, "position": 1}], "vc1"),
    "media": connection([{"id": "m1", "mediaContentType": "IMAGE", "alt": "Jar",
                          "image": {"url": f"{CDN}/jar.png?v=1", "altText": "Jar",
                                    "width": 800, "height": 1000}}]),
    "metafields": connection([{"namespace": "custom", "key": "how_to_use",
                               "type": "multi_line_text_field", "value": "Apply daily."}]),
}
MORE_VARIANTS = [{"id": "v2", "title": "3g", "sku": "C3", "price": "9.00",
                  "compareAtPrice": None, "barcode": "123", "position": 2}]

COLLECTION = {"id": "gid://shopify/Collection/5", "title": "Face Care", "handle": "face-care",
              "descriptionHtml": "<p>Face</p>", "templateSuffix": None, "sortOrder": "MANUAL",
              "image": {"url": f"{CDN}/face.jpg?v=2", "altText": "", "width": 10, "height": 10},
              "seo": {"title": None, "description": None}, "ruleSet": None,
              "products": connection([{"handle": "rose-face-cream"}])}

MENUS = [{"id": "gid://shopify/Menu/1", "handle": "main-menu", "title": "Main menu", "items": [
    {"id": "i1", "title": "Home", "type": "FRONTPAGE", "url": "/", "resourceId": None, "tags": [],
     "items": []},
    {"id": "i2", "title": "Our Collection", "type": "PAGE", "url": "/pages/our-story",
     "resourceId": "gid://shopify/Page/2", "tags": [], "items": [
         {"id": "i3", "title": "Face Care", "type": "COLLECTION", "url": "/collections/face-care",
          "resourceId": "gid://shopify/Collection/5", "tags": [], "items": []}]},
]}]

FILES_PAGE_1 = [{"id": "gid://shopify/MediaImage/11", "__typename": "MediaImage", "alt": "",
                 "fileStatus": "READY", "createdAt": "2026-01-01T00:00:00Z", "mimeType": "image/png",
                 "image": {"url": f"{CDN}/bg3.png?v=7", "width": 1200, "height": 800, "altText": ""},
                 "originalSource": {"fileSize": 4}}]
FILES_PAGE_2 = [{"id": "gid://shopify/GenericFile/12", "__typename": "GenericFile", "alt": "Cert",
                 "fileStatus": "READY", "createdAt": "2026-01-01T00:00:00Z",
                 "mimeType": "application/pdf", "url": f"{CDN}/cert.pdf?v=1", "originalFileSize": 9}]


class FakeShopify:
    """Answers each named query; paginated ones by their `after` cursor."""

    def __init__(self):
        self.calls = []
        self.paged = {
            "Pages": ("pages", {None: ([PAGE_STORY, PAGE_CONTACT], None)}),
            "Products": ("products", {None: ([PRODUCT], None)}),
            "ProductVariants": ("node.variants", {"vc1": (MORE_VARIANTS, None)}),
            "Collections": ("collections", {None: ([COLLECTION], None)}),
            "Menus": ("menus", {None: (MENUS, None)}),
            "Blogs": ("blogs", {None: ([{"id": "gid://shopify/Blog/3", "title": "News", "handle": "news",
                                         "templateSuffix": None, "commentPolicy": "CLOSED"}], None)}),
            "Articles": ("articles", {None: ([], None)}),
            "Files": ("files", {None: (FILES_PAGE_1, "f1"), "f1": (FILES_PAGE_2, None)}),
        }

    def __call__(self, query, variables):
        name = re.search(r"query\s+(\w+)", query).group(1)
        self.calls.append((name, dict(variables)))
        if name == "Shop":
            return {"shop": {"name": "Rosart", "myshopifyDomain": "rosart.myshopify.com"}}
        path, pages = self.paged[name]
        nodes, cursor = pages[variables.get("after")]
        result = connection(nodes, cursor)
        for key in reversed(path.split(".")):
            result = {key: result}
        return result


class FakeFetch:
    def __init__(self):
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        return ("bytes of " + url.split("?")[0].rsplit("/", 1)[-1]).encode()


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture()
def exported(tmp_path):
    fetch = FakeFetch()
    summary = run_export(FakeShopify(), fetch, lambda s: None, tmp_path / "export", FIXTURE_THEME)
    return tmp_path / "export", summary, fetch


def test_writes_every_export_file(exported):
    out, _, _ = exported
    for name in ["shop", "pages", "products", "collections", "menus", "blogs", "files"]:
        assert (out / "shopify" / f"{name}.json").is_file(), name
    assert (out / "theme-content.json").is_file()
    assert (out / "images" / "manifest.json").is_file()


def test_pages_are_sorted_by_handle_with_body_and_seo(exported):
    pages = read(exported[0] / "shopify" / "pages.json")
    assert [p["handle"] for p in pages] == ["contact", "our-story"]
    story = pages[1]
    assert story["bodyHtml"].startswith("<p>Roses</p>")
    assert story["seo"] == {"title": "Our Story | Rosart", "description": None}
    assert story["templateSuffix"] == "our-story" and story["isPublished"] is True


def test_products_include_every_variant_page_media_and_metafields(exported):
    product = read(exported[0] / "shopify" / "products.json")[0]
    assert [v["sku"] for v in product["variants"]] == ["C50", "C3"]
    assert product["variants"][1]["barcode"] == "123"
    assert product["images"] == [{"url": f"{CDN}/jar.png?v=1", "altText": "Jar", "width": 800, "height": 1000}]
    assert product["metafields"][0]["key"] == "how_to_use"
    assert product["seo"]["title"] == "Cream SEO"


def test_collections_list_product_handles_in_order(exported):
    collection = read(exported[0] / "shopify" / "collections.json")[0]
    assert collection["productHandles"] == ["rose-face-cream"]
    assert collection["image"]["url"] == f"{CDN}/face.jpg?v=2"


def test_menu_items_carry_the_handle_of_the_resource_they_point_to(exported):
    menu = read(exported[0] / "shopify" / "menus.json")[0]
    home, collection_page = menu["items"]
    assert home["resourceHandle"] is None
    assert collection_page["resourceHandle"] == "our-story"
    assert collection_page["items"][0]["resourceHandle"] == "face-care"


def test_files_are_paginated_and_carry_filename_and_mime(exported):
    files = read(exported[0] / "shopify" / "files.json")
    assert [(f["filename"], f["mime"]) for f in files] == [("bg3.png", "image/png"), ("cert.pdf", "application/pdf")]
    assert files[0]["width"] == 1200


def test_downloads_every_referenced_image_once_and_only_images(exported):
    out, summary, fetch = exported
    assert sorted(u.split("?")[0].rsplit("/", 1)[-1] for u in fetch.calls) == [
        "bg3.png", "face.jpg", "field.jpg", "jar.png"]
    assert (out / "images" / "jar.png").read_bytes() == b"bytes of jar.png"
    assert summary["images"]["downloaded"] == 4


def test_manifest_maps_urls_and_shop_images_names_to_local_files(exported):
    manifest = read(exported[0] / "images" / "manifest.json")
    assert manifest["sources"][f"{CDN}/jar.png"] == "jar.png"
    assert manifest["shop_images"] == {"bg3.png": "bg3.png"}
    entry = manifest["images"]["bg3.png"]
    assert entry["bytes"] == len(b"bytes of bg3.png") and len(entry["sha256"]) == 64
    assert "shop_images:bg3.png" in entry["references"]
    assert "product:rose-face-cream" in manifest["images"]["jar.png"]["references"]


def test_unresolved_shop_images_are_reported_with_where_they_are_used(exported):
    out, summary, _ = exported
    unresolved = read(out / "images" / "manifest.json")["unresolved_shop_images"]
    assert sorted(unresolved) == ["bg3_mobile.png", "logo.png", "preset-only.png"]
    assert unresolved["logo.png"] == ["config/settings_data.json"]
    assert sorted(summary["unresolved_shop_images"]) == sorted(unresolved)


def test_rerun_downloads_nothing_and_rewrites_identical_files(tmp_path):
    out = tmp_path / "export"
    run_export(FakeShopify(), FakeFetch(), lambda s: None, out, FIXTURE_THEME)
    before = {p: p.read_bytes() for p in out.rglob("*.json")}
    fetch = FakeFetch()
    summary = run_export(FakeShopify(), fetch, lambda s: None, out, FIXTURE_THEME)
    assert fetch.calls == []
    assert summary["images"]["skipped"] == 4
    assert {p: p.read_bytes() for p in out.rglob("*.json")} == before


def test_json_output_has_sorted_keys_and_a_trailing_newline(exported):
    text = (exported[0] / "shopify" / "shop.json").read_text(encoding="utf-8")
    assert text.endswith("}\n")
    assert list(json.loads(text)) == sorted(json.loads(text))


def test_skip_images_writes_content_without_fetching(tmp_path):
    fetch = FakeFetch()
    run_export(FakeShopify(), fetch, lambda s: None, tmp_path / "export", FIXTURE_THEME, download=False)
    assert fetch.calls == []
    assert (tmp_path / "export" / "shopify" / "products.json").is_file()


def test_theme_content_json_is_written_from_the_theme(exported):
    content = read(exported[0] / "theme-content.json")
    assert [s["id"] for s in content["templates"]["templates/index.json"]["sections"]] == ["hero", "story", "unused"]
