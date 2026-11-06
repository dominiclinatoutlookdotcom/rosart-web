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


# ---------------------------------------------------------------- the file transport (writes)

import subprocess  # noqa: E402
from pathlib import Path  # noqa: E402

from fake_shopify import FakeCliProcess, FakeStore  # noqa: E402
from shopify_cli import ShopifyCliFileRunner  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
STORE = "rosart-mhfmyuxq.myshopify.com"
SOUFFLE = "Rose Face Soufflé Cream | Rosart"


def file_runner(store, allow_mutations=False):
    process = FakeCliProcess(store)
    runner = ShopifyCliFileRunner(STORE, executable="shopify", allow_mutations=allow_mutations,
                                  api_version="2026-10", run_process=process)
    return runner, process


def souffle_store():
    store = FakeStore()
    store.add_product("gid://shopify/Product/1", "souffle", seo_title="Old")
    return store


def test_file_runner_writes_ascii_escaped_variables_and_reads_utf8_output_byte_exact():
    store = souffle_store()
    runner, process = file_runner(store, allow_mutations=True)
    runner("mutation M($product: ProductUpdateInput!) { productUpdate(product: $product) { product { id } } }",
           {"product": {"id": "gid://shopify/Product/1", "seo": {"title": SOUFFLE}}})
    raw = process.variable_bytes[0]
    assert all(b < 128 for b in raw)
    assert b"Souffl\u00e9" in raw
    data = runner("query R($ids: [ID!]!) { nodes(ids: $ids) { id } }", {"ids": ["gid://shopify/Product/1"]})
    assert data["nodes"][0]["seo"]["title"].encode("utf-8") == SOUFFLE.encode("utf-8")


def test_file_runner_never_reads_stdout_through_a_pipe_and_runs_from_a_neutral_cwd():
    runner, process = file_runner(souffle_store())
    runner("query { currentAppInstallation { accessScopes { handle } } }", {})
    kwargs = process.kwargs[0]
    assert kwargs.get("stdout") is not subprocess.PIPE and not kwargs.get("capture_output")
    assert kwargs.get("stdout") is not None
    cwd = Path(kwargs["cwd"]).resolve()
    assert REPO not in cwd.parents and cwd != REPO


def test_file_runner_passes_store_version_and_output_file():
    runner, process = file_runner(souffle_store())
    runner("query { currentAppInstallation { accessScopes { handle } } }", {})
    command = process.commands[0]
    assert command[command.index("--store") + 1] == STORE
    assert command[command.index("--version") + 1] == "2026-10"
    assert "--output-file" in command and "--variable-file" in command
    assert "--allow-mutations" not in command


def test_file_runner_refuses_a_mutation_unless_mutations_are_allowed():
    runner, process = file_runner(souffle_store(), allow_mutations=False)
    with pytest.raises(ShopifyCliError):
        runner("mutation { productUpdate(product: {id: \"x\"}) { product { id } } }", {})
    assert process.commands == []


def test_file_runner_adds_allow_mutations_only_to_a_mutation():
    store = souffle_store()
    runner, process = file_runner(store, allow_mutations=True)
    runner("query { currentAppInstallation { accessScopes { handle } } }", {})
    runner("mutation M($product: ProductUpdateInput!) { productUpdate(product: $product) { product { id } } }",
           {"product": {"id": "gid://shopify/Product/1", "seo": {"title": "x"}}})
    assert "--allow-mutations" not in process.commands[0]
    assert "--allow-mutations" in process.commands[1]


def _process_writing(output: bytes, returncode: int = 0, stdout: bytes = b""):
    def process(command, **kwargs):
        out_path = command[command.index("--output-file") + 1]
        if output is not None:
            Path(out_path).write_bytes(output)
        kwargs["stdout"].write(stdout)

        class Completed:
            pass
        Completed.returncode = returncode
        return Completed()
    return process


def test_file_runner_raises_the_cli_error_document_on_failure():
    error = b'{"error": {"type": "abort", "message": "GraphQL failed", "tryMessage": "Access denied for productUpdate"}}'
    runner = ShopifyCliFileRunner(STORE, executable="shopify", run_process=_process_writing(None, 1, error))
    with pytest.raises(ShopifyCliError, match="Access denied"):
        runner("query { shop { name } }", {})


def test_file_runner_refuses_output_that_is_not_utf8_instead_of_guessing():
    runner = ShopifyCliFileRunner(STORE, executable="shopify",
                                  run_process=_process_writing('{"a": "Soufflé"}'.encode("cp1252")))
    with pytest.raises(ShopifyCliError, match="UTF-8"):
        runner("query { shop { name } }", {})


def test_file_runner_unwraps_a_data_envelope_and_raises_graphql_errors():
    ok = ShopifyCliFileRunner(STORE, executable="shopify", run_process=_process_writing(b'{"data": {"shop": {"name": "R"}}}'))
    assert ok("query { shop { name } }", {}) == {"shop": {"name": "R"}}
    bad = ShopifyCliFileRunner(STORE, executable="shopify",
                               run_process=_process_writing(b'{"data": null, "errors": [{"message": "Throttled"}]}'))
    with pytest.raises(ShopifyCliError, match="Throttled"):
        bad("query { shop { name } }", {})
