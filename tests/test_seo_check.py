"""The SEO plan checker: every rule is exercised on a small fixture plan (no network, no store)."""
import copy
import json

import pytest

from seo_check import check_plan, main

CREAM_SOURCE = (
    "Rose Face Cream 50ml. Rosart Rose Face Cream is a nourishing facial moisturizer enriched with "
    "organic rose oil, rose water and rose concrete. Made in Bulgaria, inspired by the natural "
    "heritage of the Bulgarian Rose Valley."
)
TONER_SOURCE = (
    "Rose Facial Toner 330ml. Rosart Rose Facial Toner is a refreshing facial toner formulated with "
    "Damask rose water. Made in Bulgaria."
)


def change(handle, field, after, before="", source=CREAM_SOURCE, resource="product", **extra):
    entry = {
        "id": f"{resource}/{handle}/{field}" + (f"/{extra['file']}" if "file" in extra else ""),
        "resource": resource,
        "gid": f"gid://shopify/Product/{abs(hash(handle)) % 1000}",
        "handle": handle,
        "field": field,
        "before": before,
        "after": after,
        "sources": [{"ref": "fixture", "text": source}],
    }
    entry.update(extra)
    return entry


def good_plan():
    return {
        "skus": ["RARFC50", "RARFT330"],
        "changes": [
            change("rose-face-cream-50ml", "seo.title", "Rose Face Cream 50ml, Nourishing Moisturizer | Rosart"),
            change(
                "rose-face-cream-50ml",
                "seo.description",
                "Rosart Rose Face Cream 50ml with organic rose oil, rose water and rose concrete: a nourishing "
                "facial moisturizer. Made in Bulgaria.",
            ),
            change(
                "rose-facial-toner-330ml",
                "seo.title",
                "Rose Facial Toner 330ml with Damask Rose Water | Rosart",
                source=TONER_SOURCE,
            ),
            change("rose-face-cream-50ml", "alt", "Rose Face Cream 50ml, with its box",
                   resource="product_media", file="a.png"),
            change("rose-face-cream-50ml", "alt", "Rose Face Cream 50ml, among roses in a garden",
                   resource="product_media", file="b.png"),
        ],
        "kept": [],
    }


def errors_for(plan):
    return check_plan(plan)


def test_good_plan_passes():
    assert errors_for(good_plan()) == []


def test_seo_title_over_60_characters_fails():
    plan = good_plan()
    plan["changes"][0]["after"] = "Rose Face Cream 50ml, Nourishing Facial Moisturizer for Day and Night | Rosart"
    errors = errors_for(plan)
    assert any("seo.title" in e and "60" in e for e in errors), errors


@pytest.mark.parametrize("length", [119, 156])
def test_seo_description_outside_120_to_155_fails(length):
    plan = good_plan()
    text = "Rosart Rose Face Cream 50ml with organic rose oil. "
    plan["changes"][1]["after"] = (text * 5)[:length]
    errors = errors_for(plan)
    assert any("seo.description" in e and "120-155" in e for e in errors), errors


def test_duplicate_seo_title_across_products_fails():
    plan = good_plan()
    plan["changes"][2]["after"] = plan["changes"][0]["after"]
    plan["changes"][2]["sources"] = plan["changes"][0]["sources"]
    errors = errors_for(plan)
    assert any("duplicate" in e.lower() for e in errors), errors


def test_kept_values_count_for_uniqueness():
    plan = good_plan()
    kept = copy.deepcopy(plan["changes"][0])
    kept["handle"] = "rose-face-cream-3g"
    kept["value"] = kept.pop("after")
    kept.pop("before")
    plan["kept"].append(kept)
    errors = errors_for(plan)
    assert any("duplicate" in e.lower() for e in errors), errors


def test_number_absent_from_source_fails():
    plan = good_plan()
    plan["changes"][0]["after"] = "Rose Face Cream 75ml, Nourishing Moisturizer | Rosart"
    errors = errors_for(plan)
    assert any("75ml" in e for e in errors), errors


def test_size_of_a_sibling_product_is_not_a_substring_match():
    plan = good_plan()
    plan["changes"][0]["sources"] = [{"ref": "fixture", "text": CREAM_SOURCE.replace("50ml", "500ml")}]
    errors = errors_for(plan)
    assert any("50ml" in e for e in errors), errors


def test_number_with_space_before_unit_matches_source():
    plan = good_plan()
    plan["changes"][0]["sources"] = [{"ref": "fixture", "text": CREAM_SOURCE.replace("50ml", "50 ML")}]
    assert errors_for(plan) == []


def test_material_absent_from_source_fails():
    plan = good_plan()
    plan["changes"][3]["after"] = "Rose Face Cream 50ml, glass jar with its box"
    errors = errors_for(plan)
    assert any("glass" in e for e in errors), errors


def test_brand_absent_from_source_fails():
    plan = good_plan()
    plan["changes"][0]["after"] = "Rose Face Cream 50ml, sold by ORIZEN | Rosart"
    errors = errors_for(plan)
    assert any("orizen" in e.lower() for e in errors), errors


@pytest.mark.parametrize("claim", ["Valley of Roses", "Damask", "certified organic"])
def test_claim_words_need_source_support(claim):
    plan = good_plan()
    plan["changes"][0]["after"] = f"Rose Face Cream 50ml, {claim} | Rosart"
    errors = errors_for(plan)
    assert errors, f"{claim!r} is not in the cream source, so it must be rejected"


def test_rosart_needs_source_support():
    plan = good_plan()
    plan["changes"][0]["sources"] = [{"ref": "fixture", "text": "Rose Face Cream 50ml, a moisturizer."}]
    plan["changes"][0]["after"] = "Rose Face Cream 50ml, Moisturizer | Rosart"
    errors = errors_for(plan)
    assert any("rosart" in e.lower() for e in errors), errors


def test_bulgarian_is_supported_by_bulgaria_in_source():
    plan = good_plan()
    plan["changes"][0]["after"] = "Rose Face Cream 50ml, Bulgarian Rose | Rosart"
    assert errors_for(plan) == []


def test_sku_in_title_fails():
    plan = good_plan()
    plan["changes"][0]["after"] = "RARFC50 Rose Face Cream 50ml | Rosart"
    plan["changes"][0]["sources"].append({"ref": "variant.sku", "text": "RARFC50"})
    errors = errors_for(plan)
    assert any("SKU" in e for e in errors), errors


def test_duplicate_alt_within_a_product_fails():
    plan = good_plan()
    plan["changes"][4]["after"] = plan["changes"][3]["after"]
    errors = errors_for(plan)
    assert any("alt" in e and "duplicate" in e.lower() for e in errors), errors


def test_same_alt_on_different_products_is_allowed_by_the_per_product_rule():
    plan = good_plan()
    other = copy.deepcopy(plan["changes"][3])
    other["handle"] = "rose-face-cream-3g"
    other["file"] = "c.png"
    other["id"] = "product_media/rose-face-cream-3g/alt/c.png"
    plan["changes"].append(other)
    assert errors_for(plan) == []


def test_empty_alt_fails():
    plan = good_plan()
    plan["changes"][3]["after"] = "  "
    errors = errors_for(plan)
    assert any("empty" in e for e in errors), errors


@pytest.mark.parametrize("after", [
    "<b>Rose Face Cream 50ml</b> | Rosart",
    "Rose Face Cream 50ml &amp; more | Rosart",
])
def test_html_in_seo_field_fails(after):
    plan = good_plan()
    plan["changes"][0]["after"] = after
    errors = errors_for(plan)
    assert any("plain text" in e for e in errors), errors


def test_no_op_change_fails():
    plan = good_plan()
    plan["changes"][0]["before"] = plan["changes"][0]["after"]
    errors = errors_for(plan)
    assert any("no-op" in e for e in errors), errors


def test_collection_description_must_be_two_or_three_sentences():
    plan = good_plan()
    plan["changes"].append(change("face-care", "descriptionHtml", "<p>Rosart Rose Face Cream.</p>",
                                  resource="collection"))
    errors = errors_for(plan)
    assert any("sentences" in e for e in errors), errors


def test_category_must_be_a_taxonomy_gid():
    plan = good_plan()
    plan["changes"].append(change("rose-face-cream-50ml", "category", "Face Moisturizers"))
    errors = errors_for(plan)
    assert any("TaxonomyCategory" in e for e in errors), errors


def test_entry_without_sources_fails():
    plan = good_plan()
    plan["changes"][0]["sources"] = []
    errors = errors_for(plan)
    assert any("sources" in e for e in errors), errors


def test_cli_exit_codes(tmp_path, capsys):
    good = tmp_path / "good.json"
    good.write_text(json.dumps(good_plan()), encoding="utf-8")
    assert main([str(good)]) == 0
    assert "OK" in capsys.readouterr().out

    bad_plan = good_plan()
    bad_plan["changes"][0]["after"] = "x" * 61
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(bad_plan), encoding="utf-8")
    assert main([str(bad)]) == 1
    assert "seo.title" in capsys.readouterr().out
