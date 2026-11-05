"""Tests for extracting owner-written copy from Shopify theme JSON."""
import json
from pathlib import Path

import pytest

from theme_content import (
    extract_theme_content,
    is_content_setting,
    load_theme_json,
    strip_comment_header,
)

FIXTURE_THEME = Path(__file__).parent / "fixtures" / "theme"


def test_strip_comment_header_leaves_parseable_json():
    text = (FIXTURE_THEME / "templates" / "index.json").read_text(encoding="utf-8")
    assert json.loads(strip_comment_header(text))["order"] == ["hero", "story"]


def test_strip_comment_header_keeps_plain_json_and_slashes_inside_strings():
    text = '{"link": "https://example.com/a/*b*/"}'
    assert strip_comment_header(text) == text


def test_load_theme_json_reads_a_commented_template():
    assert load_theme_json(FIXTURE_THEME / "sections" / "header-group.json")["type"] == "header"


@pytest.mark.parametrize("key,value", [
    ("text", "<p>From Soil to Skin</p>"),
    ("heading", "Disclosures"),
    ("label", "Continue shopping"),
    ("link", "shopify://collections/all"),
    ("image_1", "shopify://shop_images/bg3.png"),
    ("image_1_mobile", "shopify://shop_images/m.png"),
    ("background_image", "shopify://shop_images/bg1.png"),
    ("instagram_url", "https://www.instagram.com/rosart"),
    ("collection_list", ["face-care"]),
    ("menu", "main-menu"),
    ("custom_liquid", "<div>Partners</div>"),
    ("some_new_key", "shopify://pages/our-story"),
])
def test_content_settings_are_kept(key, value):
    assert is_content_setting(key, value)


@pytest.mark.parametrize("key,value", [
    ("background_color", "#00000026"),
    ("custom_button_background", "rgba(0,0,0,0)"),
    ("custom_button_text", "#ffffff"),
    ("text_color", "{{ settings.color_palette.background }}"),
    ("text", "<h1>{{ closest.product.title }}</h1>"),
    ("font", "var(--font-body--family)"),
    ("font_size", "1rem"),
    ("padding-block-start", 40),
    ("toggle_overlay", False),
    ("media_type_1", "image"),
    ("image_ratio", "adapt"),
    ("image_gap", 4),
    ("type_preset", "h1"),
    ("link_text_color", ""),
    ("type_case_primary_link", "none"),
    ("type_font_primary_link", "body"),
    ("placeholder", ""),
    ("text", None),
    ("collection_list", []),
])
def test_styling_and_empty_settings_are_dropped(key, value):
    assert not is_content_setting(key, value)


@pytest.fixture(scope="module")
def content():
    return extract_theme_content(FIXTURE_THEME)


def test_covers_json_templates_and_section_groups_only(content):
    assert sorted(content["templates"]) == ["templates/index.json"]
    assert sorted(content["section_groups"]) == ["sections/header-group.json"]


def test_sections_follow_the_template_order_and_unordered_ones_come_last(content):
    sections = content["templates"]["templates/index.json"]["sections"]
    assert [s["id"] for s in sections] == ["hero", "story", "unused"]
    assert [s.get("in_order", True) for s in sections] == [True, True, False]


def test_section_keeps_type_name_disabled_and_only_content_settings(content):
    hero, story, _ = content["templates"]["templates/index.json"]["sections"]
    assert hero["type"] == "hero" and hero["disabled"] is False
    assert hero["settings"] == {
        "image_1": "shopify://shop_images/bg3.png",
        "image_1_mobile": "shopify://shop_images/bg3_mobile.png",
        "heading": "From Soil to Skin",
        "instagram_url": "https://www.instagram.com/rosart",
        "collection_list": ["face-care", "face-care-copy"],
        "custom_liquid": "<div class=\"partners\">Our partners</div>",
    }
    assert "custom_css" not in hero
    assert story["name"] == "Our story" and story["settings"] == {}


def test_blocks_follow_block_order_recursively_with_static_blocks_after(content):
    hero, story, _ = content["templates"]["templates/index.json"]["sections"]
    assert [b["id"] for b in story["blocks"]] == ["button_1", "group_1"]
    button, group = story["blocks"]
    assert button["disabled"] is True
    assert button["settings"] == {"label": "Shop now", "link": "shopify://collections/all"}
    assert [(b["id"], b["settings"]["text"]) for b in group["blocks"]] == [
        ("text_2", "<p>First</p>"), ("text_1", "<p>Second</p>")]
    assert group["blocks"][0]["name"] == "Intro"
    assert "name" not in group["blocks"][1]          # t:names.* is a theme translation key
    assert [(b["id"], b.get("static", False)) for b in hero["blocks"]] == [("text_a", False), ("header-logo", True)]
    assert hero["blocks"][0]["settings"] == {}        # Liquid-only text is not owner copy


def test_section_group_carries_its_type_and_disabled_sections(content):
    group = content["section_groups"]["sections/header-group.json"]
    assert group["type"] == "header"
    announce = group["sections"][0]
    assert announce["disabled"] is True
    assert announce["blocks"][0]["settings"] == {"text": "Free shipping"}


def test_global_settings_keep_content_from_current_settings_only(content):
    assert content["global_settings"] == {
        "facebook_url": "https://www.facebook.com/rosart",
        "logo": "shopify://shop_images/logo.png",
    }
