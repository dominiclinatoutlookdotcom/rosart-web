"""R-1: the client-credentials token source and the Admin API runner.

Seams: `ClientCredentialsTokenSource` and `AdminApiRunner`, driven through the fake Shopify Admin's
opener and the fake clock. Every row of contracts/rosart-web-interfaces.md R-1 that concerns the
client-credentials path.
"""
import logging
import urllib.parse

import pytest

from shopify_admin import (
    API_VERSION,
    REQUIRED_READ_SCOPES,
    AdminApiRunner,
    ClientCredentialsTokenSource,
    Secret,
    ShopifyAdminError,
)
from shopify_cli import ShopifyCliError

from .fake_clock import FakeClock
from .fake_shopify_admin import READ_SCOPES, SHOP, FakeShopifyAdmin

SHOP_QUERY = "query Shop { shop { name } }"
DATA = {"shop": {"name": "Rosart"}}


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def fake(clock):
    return FakeShopifyAdmin(clock=clock, backend=lambda query, variables: DATA)


def build(fake, clock, deadline_in=3600.0):
    tokens = ClientCredentialsTokenSource(SHOP, fake.client_id, Secret(fake.client_secret),
                                          fake.opener, clock.mono)
    runner = AdminApiRunner(SHOP, tokens, fake.opener, clock.sleep, clock.mono() + deadline_in,
                            clock=clock.mono)
    return runner, tokens


def failure_of(call):
    with pytest.raises(ShopifyAdminError) as caught:
        call()
    return caught.value


def test_the_required_scopes_are_the_six_read_scopes_the_doctor_checks():
    from publish_doctor import REQUIRED_SCOPES
    assert tuple(REQUIRED_READ_SCOPES) == tuple(REQUIRED_SCOPES) == READ_SCOPES


def test_grant_is_form_encoded_without_scope_to_the_token_endpoint(fake, clock):
    runner, _ = build(fake, clock)
    assert runner(SHOP_QUERY, {}) == DATA
    (grant,) = fake.token_requests
    assert grant.url == f"https://{SHOP}/admin/oauth/access_token"
    assert grant.method == "POST"
    assert grant.headers["content-type"] == "application/x-www-form-urlencoded"
    assert urllib.parse.parse_qs(grant.body) == {
        "grant_type": ["client_credentials"], "client_id": [fake.client_id],
        "client_secret": [fake.client_secret]}


def test_graphql_goes_to_the_pinned_api_version_with_the_token_header(fake, clock):
    runner, _ = build(fake, clock)
    runner(SHOP_QUERY, {"x": 1})
    (request,) = fake.graphql_requests
    assert API_VERSION == "2026-10"
    assert request.url == f"https://{SHOP}/admin/api/2026-10/graphql.json"
    assert request.token == fake.issued_tokens[0]
    assert request.variables == {"x": 1}


def test_the_token_is_reused_until_30_minutes_remain_then_refreshed(fake, clock):
    runner, _ = build(fake, clock, deadline_in=10 * 86400)
    runner(SHOP_QUERY, {})
    clock.advance(23 * 3600 + 20 * 60)          # 39 min remain: still reused
    runner(SHOP_QUERY, {})
    assert len(fake.token_requests) == 1
    clock.advance(11 * 60)                      # 23 h 31 min in: under 30 min remain
    runner(SHOP_QUERY, {})
    assert len(fake.token_requests) == 2
    assert fake.graphql_requests[-1].token != fake.graphql_requests[0].token


def test_a_401_invalidates_regrants_and_retries_once(fake, clock):
    runner, _ = build(fake, clock)
    fake.fail_next(401)
    assert runner(SHOP_QUERY, {}) == DATA
    assert len(fake.token_requests) == 2
    assert len(fake.graphql_requests) == 2


def test_a_second_401_fails_shopify_access(fake, clock):
    runner, _ = build(fake, clock)
    fake.fail_next(401, times=2)
    error = failure_of(lambda: runner(SHOP_QUERY, {}))
    assert error.code == "shopify-access"
    assert len(fake.graphql_requests) == 2
    assert len(fake.token_requests) == 2


def test_a_grant_missing_a_read_scope_fails_before_the_first_query(fake, clock):
    runner, _ = build(fake, clock)
    fake.grant_scope = ",".join(s for s in READ_SCOPES if s != "read_themes")
    error = failure_of(lambda: runner(SHOP_QUERY, {}))
    assert error.code == "shopify-write-scope"
    assert "read_themes" in str(error)
    assert fake.graphql_requests == []


def test_a_grant_holding_any_write_scope_fails_before_the_first_query(fake, clock):
    runner, _ = build(fake, clock)
    fake.grant_scope = ",".join(READ_SCOPES) + ",write_products"
    error = failure_of(lambda: runner(SHOP_QUERY, {}))
    assert error.code == "shopify-write-scope"
    assert "write_products" in str(error)
    assert fake.graphql_requests == []


def test_the_optional_legal_policies_scope_is_accepted(fake, clock):
    runner, _ = build(fake, clock)
    fake.grant_scope = ",".join(READ_SCOPES) + ",read_legal_policies"
    assert runner(SHOP_QUERY, {}) == DATA


def test_shop_not_permitted_is_a_plain_code_without_the_secret(fake, clock):
    runner, _ = build(fake, clock)
    fake.grant_error = "shop_not_permitted"
    error = failure_of(lambda: runner(SHOP_QUERY, {}))
    assert error.code == "shopify-not-permitted"
    assert fake.client_secret not in str(error)
    assert fake.graphql_requests == []


def test_invalid_client_is_a_plain_code_without_the_secret(fake, clock):
    runner, _ = build(fake, clock)
    fake.client_secret = "changed-on-the-shopify-side"
    tokens = ClientCredentialsTokenSource(SHOP, fake.client_id, Secret("stale-secret-value"),
                                          fake.opener, clock.mono)
    runner = AdminApiRunner(SHOP, tokens, fake.opener, clock.sleep, clock.mono() + 60, clock=clock.mono)
    error = failure_of(lambda: runner(SHOP_QUERY, {}))
    assert error.code == "shopify-access"
    assert "stale-secret-value" not in str(error) and "changed-on-the-shopify-side" not in str(error)


def test_throttled_waits_as_computed_then_succeeds(fake, clock):
    runner, _ = build(fake, clock)
    fake.throttle_next(requested=100, available=20, restore_rate=50)   # (100 - 20) / 50 = 1.6 s
    assert runner(SHOP_QUERY, {}) == DATA
    assert clock.sleeps == [1.6]


def test_throttled_is_retried_at_most_five_times(fake, clock):
    runner, _ = build(fake, clock)
    fake.throttle_next(requested=100, available=50, restore_rate=50, times=5)
    assert runner(SHOP_QUERY, {}) == DATA
    assert clock.sleeps == [1.0] * 5
    fake.throttle_next(requested=100, available=50, restore_rate=50, times=6)
    error = failure_of(lambda: runner(SHOP_QUERY, {}))
    assert error.code == "shopify-access"
    assert "throttl" in str(error).lower()
    assert clock.sleeps == [1.0] * 10


def test_429_and_5xx_back_off_one_two_four_eight_seconds(fake, clock):
    runner, _ = build(fake, clock)
    fake.fail_next(429)
    fake.fail_next(503)
    fake.fail_next(500)
    assert runner(SHOP_QUERY, {}) == DATA
    assert clock.sleeps == [1, 2, 4]
    clock.sleeps.clear()
    fake.fail_next(502, times=5)
    error = failure_of(lambda: runner(SHOP_QUERY, {}))
    assert error.code == "shopify-access"
    assert clock.sleeps == [1, 2, 4, 8]


@pytest.mark.parametrize("query", ["mutation M { x }", "# note\nsubscription S { y }"])
def test_a_mutation_or_subscription_is_refused_before_any_request(fake, clock, query):
    runner, _ = build(fake, clock)
    with pytest.raises(ShopifyCliError, match="read-only"):
        runner(query, {})
    assert fake.requests == []


def test_graphql_errors_raise_through_unwrap(fake, clock):
    runner, _ = build(fake, clock)
    fake.errors_next(["Field nope does not exist"])
    with pytest.raises(ShopifyCliError, match="GraphQL errors: Field nope does not exist"):
        runner(SHOP_QUERY, {})


def test_the_token_never_appears_in_logs_exceptions_or_reprs(fake, clock, caplog):
    caplog.set_level(logging.DEBUG)
    runner, tokens = build(fake, clock)
    fake.fail_next(503)
    runner(SHOP_QUERY, {})
    token = fake.graphql_requests[-1].token
    assert token
    fake.fail_next(401, times=2)
    error = failure_of(lambda: runner(SHOP_QUERY, {}))
    fake.grant_scope = "read_content,write_products"
    clock.advance(86000)
    scope_error = failure_of(lambda: runner(SHOP_QUERY, {}))
    secrets = [fake.client_secret] + [r.token for r in fake.graphql_requests if r.token]
    haystack = " ".join([caplog.text, str(error), repr(error), str(scope_error), repr(runner), repr(tokens),
                         str(tokens)])
    for secret in secrets:
        assert secret not in haystack


def test_secret_holder_redacts_in_repr_str_and_format():
    secret = Secret("shpat_planted")
    assert repr(secret) == str(secret) == f"{secret}" == "[redacted]"
    assert secret.reveal() == "shpat_planted"


def test_past_the_deadline_it_raises_before_the_next_request(fake, clock):
    runner, _ = build(fake, clock, deadline_in=10)
    runner(SHOP_QUERY, {})
    sent = len(fake.requests)
    clock.advance(11)
    error = failure_of(lambda: runner(SHOP_QUERY, {}))
    assert error.code == "deadline"
    assert len(fake.requests) == sent


def test_the_wall_clock_is_ignored_by_the_deadline_and_token_expiry(fake, clock):
    runner, _ = build(fake, clock, deadline_in=100)
    runner(SHOP_QUERY, {})
    clock.step_wall(10 * 3600)
    runner(SHOP_QUERY, {})
    assert len(fake.token_requests) == 1
