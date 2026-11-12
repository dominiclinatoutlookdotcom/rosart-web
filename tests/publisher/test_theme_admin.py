"""R-2 / C12: the theme read through the Admin API (`theme.files`), written to a folder.

Seam: `read_theme(run, fetch_url, theme_id, dest)` with the real `AdminApiRunner` over the fake
Shopify Admin's opener. Fixtures: tests/fixtures/theme (text) and tests/fixtures/theme_binary
(a PNG and a WOFF2-shaped file).
"""
import hashlib
from pathlib import Path

import pytest

from shopify_admin import AdminApiRunner, ClientCredentialsTokenSource, Secret, ShopifyAdminError
from theme_admin import THEME_URL_HOSTS, read_theme

from fake_clock import FakeClock
from fake_shopify_admin import SHOP, FakeShopifyAdmin

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
THEME_ID = "201570156625"
THEME_GID = f"gid://shopify/OnlineStoreTheme/{THEME_ID}"


def fixture_tree() -> dict[str, bytes]:
    tree = {path.relative_to(FIXTURES / "theme").as_posix(): path.read_bytes()
            for path in (FIXTURES / "theme").rglob("*") if path.is_file()}
    tree.update({f"assets/{path.name}": path.read_bytes()
                 for path in (FIXTURES / "theme_binary").iterdir() if path.is_file()})
    return tree


def folder_tree(root: Path) -> dict[str, bytes]:
    return {path.relative_to(root).as_posix(): path.read_bytes() for path in root.rglob("*") if path.is_file()}


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def fake(clock):
    return FakeShopifyAdmin(clock=clock)


def runner_for(fake, clock):
    tokens = ClientCredentialsTokenSource(SHOP, fake.client_id, Secret(fake.client_secret), fake.opener, clock.mono)
    return AdminApiRunner(SHOP, tokens, fake.opener, clock.sleep, clock.mono() + 3600, clock=clock.mono)


def read(fake, clock, dest, **kwargs):
    return read_theme(runner_for(fake, clock), fake.fetch, THEME_ID, dest, **kwargs)


def failure_of(fake, clock, dest, **kwargs):
    with pytest.raises(ShopifyAdminError) as caught:
        read(fake, clock, dest, **kwargs)
    return caught.value


@pytest.fixture()
def dest(tmp_path):
    folder = tmp_path / "export" / "theme"
    folder.mkdir(parents=True)
    (folder / "old-only.txt").write_text("stays untouched on failure", encoding="utf-8")
    return folder


def assert_untouched(dest):
    assert folder_tree(dest) == {"old-only.txt": b"stays untouched on failure"}
    assert sorted(p.name for p in dest.parent.iterdir()) == ["theme"], "no temp folder left behind"


def body_types(kind, names):
    if kind == "mixed":
        return {"assets/pixel.png": "base64", "assets/font.woff2": "url"}
    return {name: kind for name in names}


@pytest.mark.parametrize("kind", ["text-and-base64", "base64", "url", "mixed"])
def test_text_base64_and_url_bodies_give_a_byte_equal_tree(fake, clock, dest, kind):
    tree = fixture_tree()
    types = {"text-and-base64": {"assets/pixel.png": "base64", "assets/font.woff2": "base64"}}.get(kind) \
        or body_types(kind, tree)
    fake.add_theme(THEME_GID, files=tree, body_types=types, page_size=2)
    read(fake, clock, dest)
    assert folder_tree(dest) == tree
    assert ("url" in types.values()) == bool(fake.fetched_urls)


def test_it_pages_until_has_next_page_is_false(fake, clock, dest):
    fake.add_theme(THEME_GID, files=fixture_tree(), page_size=2)     # 5 files: three pages
    read(fake, clock, dest)
    theme_requests = [r for r in fake.graphql_requests if "theme" in r.query]
    assert len(theme_requests) == 3
    assert [r.variables.get("after") for r in theme_requests] == [None, "2", "4"]
    assert all(r.variables["id"] == THEME_GID for r in theme_requests)


def test_a_stale_file_in_the_old_tree_is_gone_afterwards(fake, clock, dest):
    fake.add_theme(THEME_GID, files=fixture_tree())
    read(fake, clock, dest)
    assert "old-only.txt" not in folder_tree(dest)
    assert sorted(p.name for p in dest.parent.iterdir()) == ["theme"]


def test_dest_is_created_when_it_does_not_exist(fake, clock, tmp_path):
    fake.add_theme(THEME_GID, files=fixture_tree())
    read(fake, clock, tmp_path / "fresh" / "theme")
    assert folder_tree(tmp_path / "fresh" / "theme") == fixture_tree()


@pytest.mark.parametrize("override", [
    {"checksumMd5": "0" * 32}, {"size": "99"}, {"size": "abc"}, {"size": None}, {"checksumMd5": None},
    {"size": "99", "checksumMd5": "0" * 32}])
def test_shopifys_size_and_md5_are_not_checked_the_bytes_are_written_as_returned(fake, clock, dest, override):
    # Owner ruling 2026-11-12: size/checksumMd5 describe a stored form the API does not always serve.
    fake.add_theme(THEME_GID, files={"layout/theme.liquid": b"abc"}, overrides={"layout/theme.liquid": override})
    read(fake, clock, dest)
    assert folder_tree(dest) == {"layout/theme.liquid": b"abc"}


def test_the_query_no_longer_asks_for_size_or_checksum(fake, clock, dest):
    fake.add_theme(THEME_GID, files={"a.txt": b"abc"})
    read(fake, clock, dest)
    query = [r for r in fake.graphql_requests if "theme" in r.query][0].query
    assert "checksumMd5" not in query and " size " not in query


def test_user_errors_fail_without_touching_dest(fake, clock, dest):
    fake.add_theme(THEME_GID, files={"a.txt": b"abc"}, user_errors=[{"code": "ACCESS_DENIED", "filename": "b.txt"}])
    error = failure_of(fake, clock, dest)
    assert error.code == "shopify-access"
    assert "b.txt" in str(error)
    assert_untouched(dest)


@pytest.mark.parametrize("name", ["../x", "/abs", "a\\b", "mystery/x.txt", "assets/../../x", "", "assets//x"])
def test_an_unsafe_file_name_fails_without_touching_dest(fake, clock, dest, name):
    fake.add_theme(THEME_GID, files={"templates/index.json": b"{}", name: b"x"})
    error = failure_of(fake, clock, dest)
    assert error.code == "theme-checksum"
    assert_untouched(dest)
    assert not (dest.parent.parent / "x").exists()


@pytest.mark.parametrize("name", ["assets/a.css", "blocks/b.liquid", "config/c.json", "layout/theme.liquid",
                                  "locales/en.json", "sections/s.liquid", "snippets/s.liquid",
                                  "templates/customers/login.json", "README.md"])
def test_known_top_folders_and_top_level_files_are_allowed(fake, clock, dest, name):
    body = b"{}" if name.endswith(".json") else b"x"      # a JSON theme file must parse
    fake.add_theme(THEME_GID, files={name: body})
    read(fake, clock, dest)
    assert folder_tree(dest) == {name: body}


def test_a_missing_theme_fails_theme_missing(fake, clock, dest):
    assert failure_of(fake, clock, dest).code == "theme-missing"
    assert_untouched(dest)


def test_a_theme_that_is_not_main_fails_theme_not_live_unless_allowed(fake, clock, dest):
    fake.add_theme(THEME_GID, files={"a.txt": b"abc"}, role="UNPUBLISHED")
    assert failure_of(fake, clock, dest).code == "theme-not-live"
    assert_untouched(dest)
    read(fake, clock, dest, require_main=False)
    assert folder_tree(dest) == {"a.txt": b"abc"}


def test_the_allowlist_is_one_constant_starting_with_the_shopify_cdn():
    assert THEME_URL_HOSTS == ("cdn.shopify.com",)


@pytest.mark.parametrize("host", ["evil.example.com", "cdn.shopify.com.evil.example", "cdn2.shopify.com"])
def test_a_url_body_on_another_host_fails_egress_refused_naming_it(fake, clock, dest, host):
    fake.url_host = host
    fake.add_theme(THEME_GID, files={"assets/a.png": b"\x89PNG\x00"}, body_types={"assets/a.png": "url"})
    error = failure_of(fake, clock, dest)
    assert error.code == "egress-refused"
    assert host in str(error)
    assert fake.fetched_urls == []
    assert_untouched(dest)


def test_the_manifest_lists_filename_sha256_and_size_sorted(fake, clock, dest):
    tree = fixture_tree()
    fake.add_theme(THEME_GID, files=tree, body_types={"assets/font.woff2": "url"})
    manifest = read(fake, clock, dest)
    expected = [{"filename": name, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
                for name, data in sorted(tree.items())]
    assert manifest.as_list() == expected


# --- JSON theme files as the live API serves them (2026-11-12): header comment + indented JSON, and a size and
# MD5 of a stored form that is never served. They must download and be written as returned. ---

HEADER = ("/*\n * ------------------------------------------------------------\n * IMPORTANT: auto-generated.\n"
          " * ------------------------------------------------------------\n */\n")
SETTINGS = HEADER + '{\n  "current": {\n    "colors": "x"\n  }\n}\n'
INDEX = HEADER + ('{\n  "sections": {\n    "hero": {\n      "type": "text",\n      "settings": {\n'
                  '        "text": "<p>Hello</p>"\n      }\n    }\n  },\n  "order": [\n    "hero"\n  ]\n}\n')


def test_a_json_file_with_header_comment_and_indented_body_and_a_different_size_and_md5_downloads(fake, clock, dest):
    files = {"config/settings_data.json": SETTINGS.encode(), "templates/index.json": INDEX.encode()}
    fake.add_theme(THEME_GID, files=files)
    for node in fake._theme({"id": THEME_GID})["files"]["nodes"]:     # the fake reproduces the live mismatch
        data = files[node["filename"]]
        assert int(node["size"]) != len(data) and node["checksumMd5"] != hashlib.md5(data).hexdigest()
    read(fake, clock, dest)
    assert folder_tree(dest) == files


def test_downloaded_commented_json_theme_files_feed_the_theme_content_extraction(fake, clock, dest):
    from theme_content import extract_theme_content, load_theme_json
    fake.add_theme(THEME_GID, files={"config/settings_data.json": SETTINGS.encode(),
                                     "templates/index.json": INDEX.encode()})
    read(fake, clock, dest)
    assert load_theme_json(dest / "config" / "settings_data.json") == {"current": {"colors": "x"}}
    content = extract_theme_content(dest)
    assert "Hello" in str(content)


def test_a_base64_json_body_is_still_decoded_and_a_bad_one_still_fails(fake, clock, dest):
    fake.add_theme(THEME_GID, files={"assets/data.json": b'{"a":1}'}, body_types={"assets/data.json": "base64"})
    read(fake, clock, dest)
    assert folder_tree(dest) == {"assets/data.json": b'{"a":1}'}
    fake.add_theme(THEME_GID, files={"assets/data.json": b"x"}, body_types={"assets/data.json": "base64"},
                   overrides={"assets/data.json": {"body": {"contentBase64": "***"}}})
    assert failure_of(fake, clock, dest).code == "theme-checksum"
