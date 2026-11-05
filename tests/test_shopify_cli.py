"""Tests for the Shopify CLI seam: output parsing, the read-only guard and pagination."""
import json

import pytest

from shopify_cli import (
    ShopifyCliError,
    assert_read_only,
    complete_connection,
    paginate,
    parse_cli_output,
)

PROGRESS = (
    '{"type":"progress","status":"started","message":"Loading stored store auth"}\n'
    '{"type":"progress","status":"completed","message":"Loading stored store auth"}\n'
)


def test_parse_returns_pretty_document_printed_after_progress_lines():
    data = {"shop": {"name": "Rosart"}}
    out = PROGRESS + json.dumps(data, indent=2) + "\n"
    assert parse_cli_output(out) == data


def test_parse_plain_document_without_progress_lines():
    assert parse_cli_output('{"pages": {"nodes": []}}') == {"pages": {"nodes": []}}


def test_parse_raises_on_cli_error_document():
    out = PROGRESS + json.dumps({"error": {"type": "abort", "message": "GraphQL operation failed.",
                                           "tryMessage": "Access denied for shopPolicies field."}})
    with pytest.raises(ShopifyCliError, match="Access denied"):
        parse_cli_output(out)


def test_parse_raises_when_there_is_no_result_document():
    with pytest.raises(ShopifyCliError):
        parse_cli_output(PROGRESS)


@pytest.mark.parametrize("query", [
    "mutation { productDelete(input: {id: \"x\"}) { deletedProductId } }",
    "  # comment\n mutation Foo { x }",
    "MUTATION { x }",
])
def test_read_only_guard_refuses_mutations(query):
    with pytest.raises(ShopifyCliError):
        assert_read_only(query)


@pytest.mark.parametrize("query", [
    "{ shop { name } }",
    "query Pages($after: String) { pages(first: 50, after: $after) { nodes { id } } }",
    "# mutation in a comment only\nquery { shop { name } }",
])
def test_read_only_guard_accepts_queries(query):
    assert_read_only(query)


class FakeRunner:
    """Serves pre-baked connection pages keyed by the `after` cursor."""

    def __init__(self, pages_by_cursor, path):
        self.pages_by_cursor = pages_by_cursor
        self.path = path
        self.calls = []

    def __call__(self, query, variables):
        self.calls.append(dict(variables))
        nodes, next_cursor = self.pages_by_cursor[variables.get("after")]
        connection = {"nodes": nodes,
                      "pageInfo": {"hasNextPage": next_cursor is not None, "endCursor": next_cursor}}
        result = connection
        for key in reversed(self.path):
            result = {key: result}
        return result


def test_paginate_follows_cursors_until_last_page():
    runner = FakeRunner({None: ([{"id": 1}, {"id": 2}], "c1"),
                         "c1": ([{"id": 3}], "c2"),
                         "c2": ([{"id": 4}], None)}, ["products"])
    nodes = paginate(runner, "query", ["products"])
    assert [n["id"] for n in nodes] == [1, 2, 3, 4]
    assert [c.get("after") for c in runner.calls] == [None, "c1", "c2"]


def test_paginate_passes_extra_variables_and_reaches_nested_paths():
    runner = FakeRunner({None: ([{"handle": "a"}], "c1"), "c1": ([{"handle": "b"}], None)},
                        ["node", "products"])
    nodes = paginate(runner, "query", ["node", "products"], variables={"id": "gid://x"})
    assert [n["handle"] for n in nodes] == ["a", "b"]
    assert all(c["id"] == "gid://x" for c in runner.calls)


def test_paginate_refuses_a_repeated_cursor_instead_of_looping_forever():
    runner = FakeRunner({None: ([{"id": 1}], "c1"), "c1": ([{"id": 2}], "c1")}, ["files"])
    with pytest.raises(ShopifyCliError, match="cursor"):
        paginate(runner, "query", ["files"])


def test_complete_connection_returns_nodes_when_there_is_no_next_page():
    connection = {"nodes": [{"id": 1}], "pageInfo": {"hasNextPage": False, "endCursor": "c0"}}
    runner = FakeRunner({}, ["node", "variants"])
    assert complete_connection(runner, connection, "query", ["node", "variants"], "gid://p") == [{"id": 1}]
    assert runner.calls == []


def test_complete_connection_fetches_the_remaining_nested_pages():
    connection = {"nodes": [{"id": 1}], "pageInfo": {"hasNextPage": True, "endCursor": "c0"}}
    runner = FakeRunner({"c0": ([{"id": 2}], "c1"), "c1": ([{"id": 3}], None)}, ["node", "variants"])
    nodes = complete_connection(runner, connection, "query", ["node", "variants"], "gid://p")
    assert [n["id"] for n in nodes] == [1, 2, 3]
    assert runner.calls[0] == {"id": "gid://p", "after": "c0"}
