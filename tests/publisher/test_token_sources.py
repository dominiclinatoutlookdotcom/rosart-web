"""C4: one TokenSource contract, two implementations, chosen by ROSART_SHOPIFY_TOKEN_MODE.

The same contract cases run against both modes; the stored-token and configuration cases follow.
"""
import logging

import pytest

from shopify_admin import (
    AdminApiRunner,
    ClientCredentialsTokenSource,
    Secret,
    ShopifyAdminError,
    StoredTokenSource,
    TokenConfigError,
    token_source_from_config,
)
from shopify_cli import ShopifyCliError

from fake_clock import FakeClock
from fake_shopify_admin import READ_SCOPES, SHOP, FakeShopifyAdmin

SHOP_QUERY = "query Shop { shop { name } }"
DATA = {"shop": {"name": "Rosart"}}
STORED = "shpat_stored_planted"
MODES = ["client-credentials", "stored-token"]


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def fake(clock):
    fake = FakeShopifyAdmin(clock=clock, backend=lambda query, variables: DATA)
    fake.stored_token = STORED
    return fake


def config_for(mode, fake):
    if mode == "client-credentials":
        return {"ROSART_SHOPIFY_CLIENT_ID": fake.client_id, "ROSART_SHOPIFY_CLIENT_SECRET": fake.client_secret}
    return {"ROSART_SHOPIFY_TOKEN_MODE": "stored-token", "ROSART_SHOPIFY_ACCESS_TOKEN": STORED}


def build(mode, fake, clock, deadline_in=3600.0):
    tokens = token_source_from_config(config_for(mode, fake), shop=SHOP, opener=fake.opener, clock=clock.mono)
    runner = AdminApiRunner(SHOP, tokens, fake.opener, clock.sleep, clock.mono() + deadline_in, clock=clock.mono)
    return runner, tokens


def set_scopes(mode, fake, scopes):
    if mode == "client-credentials":
        fake.grant_scope = ",".join(scopes)
    else:
        fake.stored_scopes = list(scopes)


def content_requests(fake):
    return [r for r in fake.graphql_requests if "currentAppInstallation" not in r.query]


def failure_of(call):
    with pytest.raises(ShopifyAdminError) as caught:
        call()
    return caught.value


@pytest.mark.parametrize("mode", MODES)
def test_both_modes_return_the_data_of_a_read_query(mode, fake, clock):
    runner, tokens = build(mode, fake, clock)
    assert runner(SHOP_QUERY, {}) == DATA
    assert isinstance(tokens, ClientCredentialsTokenSource if mode == "client-credentials" else StoredTokenSource)


@pytest.mark.parametrize("mode", MODES)
def test_a_write_scope_fails_before_the_first_content_query(mode, fake, clock):
    runner, _ = build(mode, fake, clock)
    set_scopes(mode, fake, list(READ_SCOPES) + ["write_themes"])
    error = failure_of(lambda: runner(SHOP_QUERY, {}))
    assert error.code == "shopify-write-scope"
    assert "write_themes" in str(error)
    assert content_requests(fake) == []


@pytest.mark.parametrize("mode", MODES)
def test_a_missing_read_scope_fails_before_the_first_content_query(mode, fake, clock):
    runner, _ = build(mode, fake, clock)
    set_scopes(mode, fake, [s for s in READ_SCOPES if s != "read_metaobjects"])
    error = failure_of(lambda: runner(SHOP_QUERY, {}))
    assert error.code == "shopify-write-scope"
    assert "read_metaobjects" in str(error)
    assert content_requests(fake) == []


@pytest.mark.parametrize("mode", MODES)
def test_the_optional_legal_policies_scope_is_accepted(mode, fake, clock):
    runner, _ = build(mode, fake, clock)
    set_scopes(mode, fake, list(READ_SCOPES) + ["read_legal_policies"])
    assert runner(SHOP_QUERY, {}) == DATA


@pytest.mark.parametrize("mode", MODES)
def test_a_mutation_is_refused_before_any_request(mode, fake, clock):
    runner, _ = build(mode, fake, clock)
    with pytest.raises(ShopifyCliError, match="read-only"):
        runner("mutation M { x }", {})
    assert fake.requests == []


@pytest.mark.parametrize("mode", MODES)
def test_graphql_errors_raise_and_the_url_carries_the_pinned_version(mode, fake, clock):
    runner, _ = build(mode, fake, clock)
    runner(SHOP_QUERY, {})
    fake.errors_next(["Field nope does not exist"])
    with pytest.raises(ShopifyCliError, match="GraphQL errors"):
        runner(SHOP_QUERY, {})
    assert all("/admin/api/2026-10/" in r.url for r in fake.graphql_requests)


@pytest.mark.parametrize("mode", MODES)
def test_past_the_deadline_it_raises_before_the_next_request(mode, fake, clock):
    runner, _ = build(mode, fake, clock, deadline_in=10)
    runner(SHOP_QUERY, {})
    sent = len(fake.requests)
    clock.advance(11)
    assert failure_of(lambda: runner(SHOP_QUERY, {})).code == "deadline"
    assert len(fake.requests) == sent


@pytest.mark.parametrize("mode", MODES)
def test_the_token_never_appears_in_logs_exceptions_or_reprs(mode, fake, clock, caplog):
    caplog.set_level(logging.DEBUG)
    runner, tokens = build(mode, fake, clock)
    runner(SHOP_QUERY, {})
    fake.fail_next(401, times=2)
    error = failure_of(lambda: runner(SHOP_QUERY, {}))
    set_scopes(mode, fake, list(READ_SCOPES) + ["write_themes"])
    clock.advance(86000)
    other = AdminApiRunner(SHOP, build(mode, fake, clock)[1], fake.opener, clock.sleep, clock.mono() + 60,
                           clock=clock.mono)
    scope_error = failure_of(lambda: other(SHOP_QUERY, {}))
    secrets = [fake.client_secret, STORED] + [r.token for r in fake.graphql_requests if r.token]
    haystack = " ".join([caplog.text, str(error), repr(error), str(scope_error), repr(runner), repr(tokens)])
    assert all(secret not in haystack for secret in secrets)


def test_stored_token_reads_its_scopes_before_the_first_content_query_and_only_once(fake, clock):
    runner, _ = build("stored-token", fake, clock)
    runner(SHOP_QUERY, {})
    runner(SHOP_QUERY, {})
    queries = ["currentAppInstallation" in r.query for r in fake.graphql_requests]
    assert queries == [True, False, False]
    assert {r.token for r in fake.graphql_requests} == {STORED}


def test_stored_token_never_calls_the_token_endpoint(fake, clock):
    runner, _ = build("stored-token", fake, clock)
    runner(SHOP_QUERY, {})
    fake.fail_next(401)
    failure_of(lambda: runner(SHOP_QUERY, {}))
    assert fake.token_requests == []


def test_stored_token_401_on_a_content_query_fails_shopify_access_without_retry(fake, clock):
    runner, _ = build("stored-token", fake, clock)
    runner(SHOP_QUERY, {})
    fake.fail_next(401)
    before = len(content_requests(fake))
    error = failure_of(lambda: runner(SHOP_QUERY, {}))
    assert error.code == "shopify-access"
    assert len(content_requests(fake)) == before + 1


def test_stored_token_401_on_the_scope_query_fails_shopify_access(fake, clock):
    fake.stored_token = "some-other-token"
    runner, _ = build("stored-token", fake, clock)
    error = failure_of(lambda: runner(SHOP_QUERY, {}))
    assert error.code == "shopify-access"
    assert content_requests(fake) == []
    assert STORED not in str(error)


def test_client_credentials_never_asks_for_the_stored_scope_query(fake, clock):
    runner, _ = build("client-credentials", fake, clock)
    runner(SHOP_QUERY, {})
    assert all("currentAppInstallation" not in r.query for r in fake.graphql_requests)


def test_mode_defaults_to_client_credentials():
    cfg = {"ROSART_SHOPIFY_CLIENT_ID": "id", "ROSART_SHOPIFY_CLIENT_SECRET": "secret"}
    tokens = token_source_from_config(cfg, shop=SHOP, opener=None, clock=lambda: 0.0)
    assert isinstance(tokens, ClientCredentialsTokenSource)


def test_an_unknown_mode_is_refused_at_start():
    cfg = {"ROSART_SHOPIFY_TOKEN_MODE": "oauth", "ROSART_SHOPIFY_CLIENT_ID": "id",
           "ROSART_SHOPIFY_CLIENT_SECRET": "secret"}
    with pytest.raises(TokenConfigError, match="ROSART_SHOPIFY_TOKEN_MODE"):
        token_source_from_config(cfg, shop=SHOP, opener=None, clock=lambda: 0.0)


@pytest.mark.parametrize("cfg, missing", [
    ({"ROSART_SHOPIFY_CLIENT_ID": "id"}, ["ROSART_SHOPIFY_CLIENT_SECRET"]),
    ({"ROSART_SHOPIFY_CLIENT_SECRET": "s"}, ["ROSART_SHOPIFY_CLIENT_ID"]),
    ({"ROSART_SHOPIFY_CLIENT_ID": "id", "ROSART_SHOPIFY_CLIENT_SECRET": "  "}, ["ROSART_SHOPIFY_CLIENT_SECRET"]),
    ({}, ["ROSART_SHOPIFY_CLIENT_ID", "ROSART_SHOPIFY_CLIENT_SECRET"]),
    ({"ROSART_SHOPIFY_TOKEN_MODE": "stored-token", "ROSART_SHOPIFY_CLIENT_ID": "id",
      "ROSART_SHOPIFY_CLIENT_SECRET": "s"}, ["ROSART_SHOPIFY_ACCESS_TOKEN"]),
])
def test_a_missing_key_of_the_chosen_mode_is_refused_naming_only_the_key(cfg, missing):
    with pytest.raises(TokenConfigError) as caught:
        token_source_from_config(cfg, shop=SHOP, opener=None, clock=lambda: 0.0)
    for key in missing:
        assert key in str(caught.value)
    assert "secret" not in str(caught.value).replace("SECRET", "")


def test_each_mode_starts_without_the_other_modes_keys():
    cc = token_source_from_config({"ROSART_SHOPIFY_CLIENT_ID": "id", "ROSART_SHOPIFY_CLIENT_SECRET": "s"},
                                  shop=SHOP, opener=None, clock=lambda: 0.0)
    stored = token_source_from_config({"ROSART_SHOPIFY_TOKEN_MODE": "stored-token",
                                       "ROSART_SHOPIFY_ACCESS_TOKEN": "t"},
                                      shop=SHOP, opener=None, clock=lambda: 0.0)
    assert isinstance(cc, ClientCredentialsTokenSource) and isinstance(stored, StoredTokenSource)


def test_stored_token_source_takes_a_secret_and_hides_it():
    tokens = StoredTokenSource(SHOP, Secret("shpat_x"), opener=None)
    assert "shpat_x" not in repr(tokens)
