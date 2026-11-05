"""Tests for the pure image rules: host allowlist, local filenames, reference finding and resolving."""
import pytest

from images import (
    DisallowedHost,
    assert_allowed_host,
    assign_local_names,
    canonical_url,
    filename_from_url,
    find_cdn_image_urls,
    find_shop_image_refs,
    resolve_shop_images,
)

CDN = "https://cdn.shopify.com/s/files/1/1075/6656/3409/files"


@pytest.mark.parametrize("url", [
    f"{CDN}/bg3.png?v=1",
    "https://cdn.shopify.com/s/files/1/x/products/a.jpg",
])
def test_allowlist_accepts_shopify_cdn_over_https(url):
    assert_allowed_host(url)


@pytest.mark.parametrize("url", [
    "http://cdn.shopify.com/s/files/a.png",                      # not https
    "https://shopify-shop-assets.storage.googleapis.com/a.png",  # signed originalSource host
    "https://cdn.shopify.com.evil.example/a.png",                # suffix trick
    "https://evil.example/cdn.shopify.com/a.png",                # host in the path
    "https://user@evil.example/a.png",
    "https://rosart-mhfmyuxq.myshopify.com/a.png",               # the storefront
    "file:///etc/passwd",
])
def test_allowlist_refuses_any_other_host(url):
    with pytest.raises(DisallowedHost):
        assert_allowed_host(url)


def test_filename_drops_query_string_and_decodes_percent_escapes():
    assert filename_from_url(f"{CDN}/Rose%20Balm.png?v=1793858961&width=600") == "Rose Balm.png"


def test_filename_replaces_characters_windows_cannot_store():
    assert filename_from_url(f"{CDN}/a%3Ab%7Cc.png") == "a_b_c.png"


def test_canonical_url_ignores_query_so_cache_busters_share_one_file():
    assert canonical_url(f"{CDN}/bg3.png?v=1") == canonical_url(f"{CDN}/bg3.png?v=2") == f"{CDN}/bg3.png"


def test_local_names_keep_original_filename_when_unique():
    names = assign_local_names([f"{CDN}/bg1.png?v=1", f"{CDN}/bg3.png?v=9"])
    assert names == {f"{CDN}/bg1.png": "bg1.png", f"{CDN}/bg3.png": "bg3.png"}


def test_local_names_same_path_with_different_query_is_one_file():
    names = assign_local_names([f"{CDN}/bg1.png?v=1", f"{CDN}/bg1.png?v=2"])
    assert names == {f"{CDN}/bg1.png": "bg1.png"}


def test_local_names_suffix_duplicates_by_sorted_url_regardless_of_input_order():
    a = "https://cdn.shopify.com/s/files/1/a/files/rose.png"
    b = "https://cdn.shopify.com/s/files/1/b/files/rose.png"
    c = "https://cdn.shopify.com/s/files/1/c/files/ROSE.png"   # case-only clash on Windows
    expected = {a: "rose.png", b: "rose-2.png", c: "ROSE-3.png"}
    assert assign_local_names([c, b, a]) == expected
    assert assign_local_names([a, b, c]) == expected


def test_local_names_give_the_original_name_to_the_shopify_files_copy_first():
    collection_copy = "https://cdn.shopify.com/s/files/1/1075/collections/1.png"
    files_copy = "https://cdn.shopify.com/s/files/1/1075/files/1.png?v=5"
    names = assign_local_names([collection_copy, files_copy])
    assert names == {"https://cdn.shopify.com/s/files/1/1075/files/1.png": "1.png",
                     collection_copy: "1-2.png"}


def test_local_names_suffix_does_not_collide_with_a_real_file_of_that_name():
    a = "https://cdn.shopify.com/s/files/1/a/files/rose.png"
    b = "https://cdn.shopify.com/s/files/1/b/files/rose.png"
    taken = "https://cdn.shopify.com/s/files/1/z/files/rose-2.png"
    names = assign_local_names([a, b, taken])
    assert len(set(n.lower() for n in names.values())) == 3
    assert names[taken] == "rose-2.png"


def test_find_shop_image_refs_walks_nested_settings_and_embedded_strings():
    doc = {"sections": {"hero": {"settings": {"image_1": "shopify://shop_images/bg3.png"},
                                 "blocks": {"b": {"settings": {
                                     "text": "<img src=\"shopify://shop_images/Rose_Map.png\">"}}}}},
           "list": ["shopify://shop_images/bg3.png", "shopify://collections/all", 3, None]}
    assert find_shop_image_refs(doc) == ["Rose_Map.png", "bg3.png"]


def test_find_cdn_image_urls_takes_images_from_html_and_ignores_other_assets():
    html = ('<img src="https://cdn.shopify.com/s/files/1/1/files/ARTEC.png?v=17">'
            '<link href="https://cdn.shopify.com/shopifycloud/privacy-banner/data-sale-opt-out.css">'
            "<a href='https://artechem.com/en/'>x</a>"
            '<img src="https://cdn.shopify.com/s/files/1/1/files/b.JPG">')
    assert find_cdn_image_urls(html) == [
        "https://cdn.shopify.com/s/files/1/1/files/ARTEC.png?v=17",
        "https://cdn.shopify.com/s/files/1/1/files/b.JPG",
    ]


FILES = [
    {"id": "gid://shopify/MediaImage/1", "filename": "bg3.png", "url": f"{CDN}/bg3.png?v=1"},
    {"id": "gid://shopify/MediaImage/2", "filename": "Rose_Map.png", "url": f"{CDN}/Rose_Map.png?v=2"},
]


def test_resolver_maps_each_shop_images_name_to_its_file_url():
    resolved, unresolved = resolve_shop_images(["bg3.png", "Rose_Map.png"], FILES)
    assert resolved == {"bg3.png": f"{CDN}/bg3.png?v=1", "Rose_Map.png": f"{CDN}/Rose_Map.png?v=2"}
    assert unresolved == []


def test_resolver_falls_back_to_a_case_insensitive_match():
    resolved, unresolved = resolve_shop_images(["BG3.PNG"], FILES)
    assert resolved == {"BG3.PNG": f"{CDN}/bg3.png?v=1"}
    assert unresolved == []


def test_resolver_reports_names_with_no_file():
    resolved, unresolved = resolve_shop_images(["bg3.png", "gone.png"], FILES)
    assert resolved == {"bg3.png": f"{CDN}/bg3.png?v=1"}
    assert unresolved == ["gone.png"]
