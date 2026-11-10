"""The fake Shopify Admin (token endpoint, GraphQL, theme files) behaves like the real one.

Seam: the fake's opener, the same `open(request, timeout=)` shape `urllib.request` offers.
"""
import base64
import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

from fake_clock import FakeClock
from fake_shopify_admin import READ_SCOPES, FakeShopifyAdmin

SHOP = "rosart-mhfmyuxq.myshopify.com"
GRAPHQL = f"https://{SHOP}/admin/api/2026-10/graphql.json"
TOKEN_URL = f"https://{SHOP}/admin/oauth/access_token"
THEME_GID = "gid://shopify/OnlineStoreTheme/201570156625"
SHOP_QUERY = "query Shop { shop { name } }"
SCOPES_QUERY = "query S { currentAppInstallation { accessScopes { handle } } }"


def grant(fake, **overrides):
    form = {"grant_type": "client_credentials", "client_id": fake.client_id,
            "client_secret": fake.client_secret, **overrides}
    request = urllib.request.Request(
        TOKEN_URL, data=urllib.parse.urlencode(form).encode(), method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"})
    with fake.opener.open(request, timeout=5) as response:
        return json.loads(response.read())


def graphql(fake, query, variables=None, token=None):
    request = urllib.request.Request(
        GRAPHQL, data=json.dumps({"query": query, "variables": variables or {}}).encode(), method="POST",
        headers={"Content-Type": "application/json", "X-Shopify-Access-Token": token or ""})
    with fake.opener.open(request, timeout=5) as response:
        return response.status, json.loads(response.read())


def error_of(call):
    with pytest.raises(urllib.error.HTTPError) as caught:
        call()
    exc = caught.value
    return exc.code, exc.read().decode()


@pytest.fixture()
def fake():
    return FakeShopifyAdmin(clock=FakeClock(), backend=lambda query, variables: {"shop": {"name": "Rosart"}})


def test_token_endpoint_grants_a_token_with_the_configured_scope(fake):
    body = grant(fake)
    assert body["scope"] == ",".join(READ_SCOPES)
    assert body["expires_in"] == 86399
    assert body["access_token"]
    fake.grant_scope = "read_content,write_products"
    assert grant(fake)["scope"] == "read_content,write_products"


def test_token_requests_are_recorded_with_the_form_body(fake):
    grant(fake)
    (recorded,) = fake.token_requests
    assert recorded.url == TOKEN_URL
    assert urllib.parse.parse_qs(recorded.body) == {
        "grant_type": ["client_credentials"], "client_id": [fake.client_id],
        "client_secret": [fake.client_secret]}


def test_shop_not_permitted_and_invalid_client_are_error_bodies(fake):
    fake.grant_error = "shop_not_permitted"
    status, body = error_of(lambda: grant(fake))
    assert status == 400 and json.loads(body)["error"] == "shop_not_permitted"
    fake.grant_error = None
    status, body = error_of(lambda: grant(fake, client_secret="wrong"))
    assert status in (400, 401) and json.loads(body)["error"] == "invalid_client"


def test_graphql_answers_content_queries_from_the_backend_with_cost_data(fake):
    token = grant(fake)["access_token"]
    status, body = graphql(fake, SHOP_QUERY, token=token)
    assert status == 200
    assert body["data"] == {"shop": {"name": "Rosart"}}
    assert body["extensions"]["cost"]["throttleStatus"]["maximumAvailable"] == 1000


def test_graphql_rejects_an_unknown_token_with_401(fake):
    status, _ = error_of(lambda: graphql(fake, SHOP_QUERY, token="nope"))
    assert status == 401


def test_a_token_expires_after_expires_in_on_the_clock(fake):
    token = grant(fake)["access_token"]
    fake.clock.advance(86400)
    status, _ = error_of(lambda: graphql(fake, SHOP_QUERY, token=token))
    assert status == 401


def test_revoked_tokens_are_refused(fake):
    token = grant(fake)["access_token"]
    fake.revoke_tokens()
    status, _ = error_of(lambda: graphql(fake, SHOP_QUERY, token=token))
    assert status == 401


def test_the_stored_token_is_accepted_and_its_scopes_are_listed(fake):
    fake.stored_token = "shpat_stored"
    fake.stored_scopes = ["read_content", "write_themes"]
    _, body = graphql(fake, SCOPES_QUERY, token="shpat_stored")
    assert body["data"] == {"currentAppInstallation": {
        "accessScopes": [{"handle": "read_content"}, {"handle": "write_themes"}]}}


def test_a_client_credentials_token_lists_the_scope_it_was_granted(fake):
    fake.grant_scope = "read_content"
    token = grant(fake)["access_token"]
    _, body = graphql(fake, SCOPES_QUERY, token=token)
    assert body["data"]["currentAppInstallation"]["accessScopes"] == [{"handle": "read_content"}]


def test_scripted_throttled_status_and_error_envelopes(fake):
    token = grant(fake)["access_token"]
    fake.throttle_next(requested=100, available=20, restore_rate=50)
    _, body = graphql(fake, SHOP_QUERY, token=token)
    assert body["errors"][0]["extensions"]["code"] == "THROTTLED"
    assert body["extensions"]["cost"] == {
        "requestedQueryCost": 100, "actualQueryCost": None,
        "throttleStatus": {"maximumAvailable": 1000.0, "currentlyAvailable": 20, "restoreRate": 50.0}}
    fake.fail_next(429)
    assert error_of(lambda: graphql(fake, SHOP_QUERY, token=token))[0] == 429
    fake.fail_next(503)
    assert error_of(lambda: graphql(fake, SHOP_QUERY, token=token))[0] == 503
    fake.fail_next(401)
    assert error_of(lambda: graphql(fake, SHOP_QUERY, token=token))[0] == 401
    fake.errors_next(["Field x does not exist"])
    _, body = graphql(fake, SHOP_QUERY, token=token)
    assert body["errors"] == [{"message": "Field x does not exist"}] and "data" not in body
    _, body = graphql(fake, SHOP_QUERY, token=token)
    assert body["data"] == {"shop": {"name": "Rosart"}}


def test_every_request_is_recorded_in_order_with_its_token_header(fake):
    token = grant(fake)["access_token"]
    graphql(fake, SHOP_QUERY, token=token)
    assert [r.kind for r in fake.requests] == ["token", "graphql"]
    assert fake.graphql_requests[0].url == GRAPHQL
    assert fake.graphql_requests[0].token == token
    assert "Shop" in fake.graphql_requests[0].query


THEME_QUERY = """query ThemeFiles($id: ID!, $after: String) {
  theme(id: $id) { id name role files(first: 250, after: $after) { nodes { filename size checksumMd5 body {
    ... on OnlineStoreThemeFileBodyText { content } ... on OnlineStoreThemeFileBodyBase64 { contentBase64 }
    ... on OnlineStoreThemeFileBodyUrl { url } } } pageInfo { hasNextPage endCursor } userErrors { code filename } } } }"""


def theme_pages(fake, token):
    nodes, after = [], None
    pages = 0
    while True:
        _, body = graphql(fake, THEME_QUERY, {"id": THEME_GID, "after": after}, token=token)
        files = body["data"]["theme"]["files"]
        nodes.extend(files["nodes"])
        pages += 1
        if not files["pageInfo"]["hasNextPage"]:
            return nodes, pages
        after = files["pageInfo"]["endCursor"]


def test_theme_files_page_with_text_base64_and_url_bodies(fake):
    png = bytes(range(256))
    fake.add_theme(THEME_GID, files={"layout/theme.liquid": b"<html>\xc3\xa9</html>",
                                     "assets/a.png": png, "assets/b.woff2": b"wOF2" + bytes(10)},
                   body_types={"assets/b.woff2": "url"}, page_size=1)
    token = grant(fake)["access_token"]
    nodes, pages = theme_pages(fake, token)
    assert pages == 3
    by_name = {n["filename"]: n for n in nodes}
    assert by_name["layout/theme.liquid"]["body"] == {"content": "<html>é</html>"}
    assert base64.b64decode(by_name["assets/a.png"]["body"]["contentBase64"]) == png
    url = by_name["assets/b.woff2"]["body"]["url"]
    assert url.startswith("https://cdn.shopify.com/")
    assert fake.fetch(url) == b"wOF2" + bytes(10)
    assert fake.fetched_urls == [url]
    assert by_name["assets/a.png"]["size"] == "256"
    assert by_name["assets/a.png"]["checksumMd5"] == hashlib.md5(png).hexdigest()


def test_theme_missing_gives_a_null_theme_and_overrides_corrupt_size_checksum_and_errors(fake):
    token = grant(fake)["access_token"]
    _, body = graphql(fake, THEME_QUERY, {"id": THEME_GID, "after": None}, token=token)
    assert body["data"] == {"theme": None}
    fake.add_theme(THEME_GID, role="UNPUBLISHED", files={"a.txt": b"abc"},
                   overrides={"a.txt": {"size": "99", "checksumMd5": "0" * 32}},
                   user_errors=[{"code": "ACCESS_DENIED", "filename": "b.txt"}])
    _, body = graphql(fake, THEME_QUERY, {"id": THEME_GID, "after": None}, token=token)
    theme = body["data"]["theme"]
    assert theme["role"] == "UNPUBLISHED"
    assert theme["files"]["nodes"][0]["size"] == "99"
    assert theme["files"]["nodes"][0]["checksumMd5"] == "0" * 32
    assert theme["files"]["userErrors"] == [{"code": "ACCESS_DENIED", "filename": "b.txt"}]
