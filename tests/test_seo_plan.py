"""The SEO plan builder: a fixture export, snapshot and content in, plan entries out (no network)."""
import json

import pytest

from seo_plan import build_plan, load_export, render_markdown

CDN = "https://cdn.shopify.com/s/files/1/1/files"


def product(handle, title, sku, *, seo_title, seo_desc, images, description, metafields=None):
    return {
        "id": f"gid://shopify/Product/{len(handle)}{len(title)}",
        "handle": handle,
        "title": title,
        "status": "ACTIVE",
        "vendor": "Rosart",
        "productType": "Facial Toner",
        "descriptionHtml": f"<p>{description}</p>",
        "seo": {"title": seo_title, "description": seo_desc},
        "variants": [{"sku": sku, "price": "10.00", "title": "Default Title"}],
        "media": [
            {"id": f"gid://shopify/MediaImage/{i}{len(handle)}", "alt": "",
             "image": {"altText": "", "url": f"{CDN}/{name}?v=1"}, "mediaContentType": "IMAGE"}
            for i, name in enumerate(images, start=1)
        ],
        "images": [{"altText": "", "url": f"{CDN}/{name}?v=1"} for name in images],
        "metafields": metafields or [],
    }


TONER = product(
    "rose-facial-toner-330ml", "Rose Facial Toner 330ml", "RARFT330",
    seo_title="Rosart Rose Facial Toner 330ml | Hydrating Toner",
    seo_desc="Discover Rosart Rose Facial Toner 330ml with Damask rose water. Refreshes and hydrates skin. Made in Bulgaria. Gentle daily toner.",
    images=["toner-box.png", "toner-garden.png"],
    description="Rosart Rose Facial Toner is a refreshing facial toner formulated with Damask rose water.",
    metafields=[{"namespace": "custom", "key": "certifications_brand_values", "type": "multi_line_text_field",
                 "value": "Made in Bulgaria, inspired by the Bulgarian Rose Valley."}],
)
MASK = product(
    "bulgarian-rose-hydrosol-essence-mask-5pcs", "Bulgarian Rose Hydrosol Essence Mask 5pcs", "RABRHEM5",
    seo_title=TONER["seo"]["title"], seo_desc=TONER["seo"]["description"],
    images=["mask-box.png"],
    description="Rosart Bulgarian Rose Hydrosol Essence Mask is a hydrating sheet mask. Each pack contains 5 masks.",
    metafields=[{"namespace": "custom", "key": "how_to_use", "type": "multi_line_text_field",
                 "value": "Pour onto a cotton pad (toner text)."}],
)

COLLECTION = {
    "id": "gid://shopify/Collection/1", "handle": "face-care-copy", "title": "Rose Water & Toners",
    "descriptionHtml": "", "seo": {"title": None, "description": None},
    "image": {"altText": "", "url": f"{CDN}/../collections/2.png?v=1"},
    "productHandles": ["rose-facial-toner-330ml"],
}
PAGE = {"id": "gid://shopify/Page/1", "handle": "our-story", "title": "Our Story", "bodyHtml": "",
        "isPublished": True, "seo": {"title": None, "description": None}}


@pytest.fixture
def export_dir(tmp_path):
    shop = tmp_path / "shopify"
    shop.mkdir()
    (shop / "products.json").write_text(json.dumps([TONER, MASK]), encoding="utf-8")
    (shop / "collections.json").write_text(json.dumps([COLLECTION]), encoding="utf-8")
    (shop / "pages.json").write_text(json.dumps([PAGE]), encoding="utf-8")
    (shop / "shop.json").write_text(json.dumps({"name": "Rosart"}), encoding="utf-8")
    (shop / "files.json").write_text(json.dumps([
        {"id": "gid://shopify/MediaImage/900", "alt": "", "url": f"{CDN}/logo.png?v=1", "filename": "logo.png"},
        {"id": "gid://shopify/MediaImage/901", "alt": "", "url": f"{CDN}/bg.png?v=1", "filename": "bg.png"},
    ]), encoding="utf-8")
    images = tmp_path / "images"
    images.mkdir()
    sources = {f"{CDN}/{n}": n for n in ["toner-box.png", "toner-garden.png", "mask-box.png", "logo.png", "bg.png"]}
    (images / "manifest.json").write_text(json.dumps({"sources": sources, "images": {}}), encoding="utf-8")
    (tmp_path / "theme-content.json").write_text(json.dumps({"templates": {
        "templates/page.our-story.json": {"sections": [{"blocks": [{"text": "<p>Rosart cultivates Damask roses in Bulgaria's Valley of Roses.</p>"}]}]},
        "templates/index.json": {"sections": [{"blocks": [{"text": "<p>From Our Rose Fields to Your Skin.</p>"}]}]},
    }, "section_groups": {}}), encoding="utf-8")
    return tmp_path


SNAPSHOT = {
    "access_scopes": ["read_products", "write_content"],
    "shop": {"id": "gid://shopify/Shop/1", "description": ""},
    "products": {
        "rose-facial-toner-330ml": {"category": {"id": "gid://shopify/TaxonomyCategory/hb-3-2-9-20",
                                                 "fullName": "Skin Care > Face Moisturizers"}},
        "bulgarian-rose-hydrosol-essence-mask-5pcs": {"category": {"id": "gid://shopify/TaxonomyCategory/hb-3-2-9-20",
                                                                   "fullName": "Skin Care > Face Moisturizers"}},
    },
    "collections": {"face-care-copy": {"image": {"id": "gid://shopify/CollectionImage/7", "altText": ""}}},
    "pages": {"our-story": {"title_tag": None, "description_tag": None}},
}

CONTENT = {
    "categories": {"gid://shopify/TaxonomyCategory/hb-3-2-9-17-2": "Skin Care > Toners & Astringents > Toners"},
    "products": {
        "rose-facial-toner-330ml": {
            "seo.title": "Rose Facial Toner 330ml with Damask Rose Water | Rosart",
            "category": "gid://shopify/TaxonomyCategory/hb-3-2-9-17-2",
            "alt": {"toner-box.png": "with its box", "toner-garden.png": "among roses in a garden"},
        },
        "bulgarian-rose-hydrosol-essence-mask-5pcs": {
            "exclude_metafields": True,
            "seo.title": "Bulgarian Rose Hydrosol Sheet Mask, 5 Pack | Rosart",
            "seo.description": "Rosart Bulgarian Rose Hydrosol Essence Mask: a hydrating sheet mask. Each pack contains 5 masks, for skin that needs hydration.",
            "alt": {"mask-box.png": "front of the box"},
        },
    },
    "collections": {"face-care-copy": {
        "descriptionHtml": "<p>Rosart Rose Water &amp; Toners. Rose Facial Toner 330ml has Damask rose water.</p>",
        "image.altText": "Rose Facial Toner 330ml",
    }},
    "pages": {"our-story": {"seo.title": "Our Story: Damask Roses in Bulgaria | Rosart"}},
    "shop": {"seo.title": "Rosart Bulgarian Rose Skincare"},
    "files": {"logo.png": {"alt": "Rosart", "use": "site logo", "observed": "Rosart wordmark"}},
    "decorative": {"bg.png": "section background"},
    "image_observations": {"toner-box.png": "bottle beside its box", "toner-garden.png": "bottle among roses",
                           "mask-box.png": "front of the box"},
}


@pytest.fixture
def plan(export_dir):
    return build_plan(load_export(export_dir), SNAPSHOT, CONTENT)


def entries(plan, handle=None, field=None):
    return [c for c in plan["changes"]
            if (handle is None or c["handle"] == handle) and (field is None or c["field"] == field)]


def test_before_is_the_exact_current_value(plan):
    (title,) = entries(plan, "rose-facial-toner-330ml", "seo.title")
    assert title["before"] == "Rosart Rose Facial Toner 330ml | Hydrating Toner"
    assert title["gid"] == TONER["id"]


def test_mask_gets_its_own_seo_and_not_the_toner_text(plan):
    (desc,) = entries(plan, "bulgarian-rose-hydrosol-essence-mask-5pcs", "seo.description")
    assert desc["before"] == TONER["seo"]["description"]
    assert "Toner" not in desc["after"]
    assert "Hydrosol" in desc["after"]


def test_excluded_metafields_are_not_used_as_sources(plan):
    (desc,) = entries(plan, "bulgarian-rose-hydrosol-essence-mask-5pcs", "seo.description")
    joined = " ".join(s["text"] for s in desc["sources"])
    assert "toner text" not in joined
    assert "5 masks" in joined


def test_fields_without_new_content_are_kept_not_changed(plan):
    assert entries(plan, "rose-facial-toner-330ml", "seo.description") == []
    kept = [k for k in plan["kept"] if k["handle"] == "rose-facial-toner-330ml" and k["field"] == "seo.description"]
    assert kept and kept[0]["value"] == TONER["seo"]["description"]


def test_alt_text_is_title_plus_view_with_media_gid(plan):
    alts = entries(plan, "rose-facial-toner-330ml", "alt")
    assert [a["after"] for a in alts] == ["Rose Facial Toner 330ml, with its box",
                                          "Rose Facial Toner 330ml, among roses in a garden"]
    assert alts[0]["gid"].startswith("gid://shopify/MediaImage/")
    assert alts[0]["file"] == "toner-box.png"
    assert any("bottle beside its box" in s["text"] for s in alts[0]["sources"])


def test_category_change_records_labels(plan):
    (cat,) = entries(plan, "rose-facial-toner-330ml", "category")
    assert cat["before"] == "gid://shopify/TaxonomyCategory/hb-3-2-9-20"
    assert cat["after"] == "gid://shopify/TaxonomyCategory/hb-3-2-9-17-2"
    assert cat["after_label"].endswith("Toners")


def test_collection_page_shop_and_file_entries(plan):
    assert entries(plan, "face-care-copy", "descriptionHtml")[0]["before"] == ""
    image = entries(plan, "face-care-copy", "image.altText")[0]
    assert image["gid"] == "gid://shopify/CollectionImage/7"
    assert entries(plan, "our-story", "seo.title")[0]["mechanism"].startswith("pageUpdate")
    shop = entries(plan, field="seo.title")
    assert any(c["resource"] == "shop" and c["mechanism"].startswith("manual") for c in shop)
    (logo,) = [c for c in plan["changes"] if c["resource"] == "file"]
    assert logo["gid"] == "gid://shopify/MediaImage/900" and logo["after"] == "Rosart"
    assert plan["decorative"] == [{"file": "bg.png", "gid": "gid://shopify/MediaImage/901",
                                   "reason": "section background"}]


def test_never_touches_protected_fields(plan):
    allowed = {"seo.title", "seo.description", "alt", "category", "vendor", "productType",
               "descriptionHtml", "image.altText"}
    assert {c["field"] for c in plan["changes"]} <= allowed


def test_skus_are_listed_for_the_checker(plan):
    assert plan["skus"] == ["RABRHEM5", "RARFT330"]


def test_required_scopes_are_derived_from_changes(plan):
    scopes = plan["scopes"]
    assert scopes["current"] == ["read_products", "write_content"]
    assert set(scopes["missing"]) == {"write_products", "write_files"}


def test_order_is_deterministic(export_dir):
    a = build_plan(load_export(export_dir), SNAPSHOT, CONTENT)
    b = build_plan(load_export(export_dir), SNAPSHOT, CONTENT)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    ids = [c["id"] for c in a["changes"]]
    assert ids == sorted(ids, key=lambda i: (["shop", "page", "collection", "product", "product_media", "file"].index(i.split("/")[0]), i))


def test_markdown_shows_before_after_and_counts(plan):
    md = render_markdown(plan)
    # Table cells escape the pipe so the "| Rosart" suffix does not split the cell.
    assert "Rosart Rose Facial Toner 330ml \\| Hydrating Toner" in md
    assert "Rose Facial Toner 330ml with Damask Rose Water \\| Rosart" in md
    assert "write_products" in md
    assert "(48 → 55)" in md
