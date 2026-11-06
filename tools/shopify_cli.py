"""Access to the Shopify Admin GraphQL API through `shopify store execute`.

The export never talks to Shopify directly: it shells out to the authenticated Shopify CLI, and
its `ShopifyCliRunner` refuses mutations. `ShopifyCliFileRunner` is the file-only transport the
owner-approved SEO apply step (tools/seo_apply.py) uses; it allows mutations only when opened
with ``allow_mutations=True``.
Everything here is built around a *runner*, a callable ``(query, variables) -> data dict``, so
tests can inject a fake and never touch the network.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Callable, Optional

Runner = Callable[[str, dict], dict]

# A page loop that runs this long is a bug (or a cursor that never advances), not a big store.
MAX_PAGES = 1000


class ShopifyCliError(RuntimeError):
    """The CLI failed, returned a GraphQL error, or a request was refused locally."""


def assert_read_only(query: str) -> None:
    """Refuse anything that is not a plain query: this tool must never mutate the store."""
    without_comments = re.sub(r"#[^\n]*", "", query)
    if re.match(r"\s*(mutation|subscription)\b", without_comments, flags=re.IGNORECASE):
        raise ShopifyCliError("refusing to run a mutation or subscription: the export is read-only")


def parse_cli_output(stdout: str) -> dict:
    """Return the result document from `shopify store execute --json` output.

    The CLI may print ``{"type": "progress", ...}`` lines before the pretty-printed result, so
    the output is decoded as a sequence of JSON documents and the last non-progress one wins.
    A ``{"error": {...}}`` document is raised as :class:`ShopifyCliError`.
    """
    decoder = json.JSONDecoder()
    documents: list[Any] = []
    index = 0
    length = len(stdout)
    while index < length:
        while index < length and stdout[index].isspace():
            index += 1
        if index >= length:
            break
        try:
            document, index = decoder.raw_decode(stdout, index)
        except json.JSONDecodeError:
            # Skip a non-JSON line (a stray warning) and carry on with the next one.
            newline = stdout.find("\n", index)
            index = length if newline == -1 else newline + 1
            continue
        documents.append(document)

    results = [d for d in documents if not (isinstance(d, dict) and d.get("type") == "progress")]
    if not results:
        raise ShopifyCliError("the Shopify CLI printed no result document")
    result = results[-1]
    if isinstance(result, dict) and isinstance(result.get("error"), dict):
        error = result["error"]
        detail = error.get("tryMessage") or error.get("message") or json.dumps(error)
        raise ShopifyCliError(f"Shopify CLI error: {detail}")
    if not isinstance(result, dict):
        raise ShopifyCliError(f"unexpected Shopify CLI result: {result!r:.200}")
    return result


def _dig(data: dict, path: list[str]) -> dict:
    node: Any = data
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise ShopifyCliError(f"response has no {'.'.join(path)} (stopped at {key!r})")
        node = node[key]
    if not isinstance(node, dict):
        raise ShopifyCliError(f"{'.'.join(path)} is not a connection")
    return node


def paginate(run: Runner, query: str, path: list[str], variables: Optional[dict] = None,
             after: Optional[str] = None) -> list[dict]:
    """Collect every node of the connection at ``path`` by following ``pageInfo`` cursors.

    The query must take an ``$after: String`` variable and select
    ``nodes`` and ``pageInfo { hasNextPage endCursor }`` on the connection.
    """
    nodes: list[dict] = []
    seen_cursors: set[str] = set()
    cursor = after
    for _ in range(MAX_PAGES):
        page_variables = dict(variables or {})
        page_variables["after"] = cursor
        connection = _dig(run(query, page_variables), path)
        nodes.extend(connection.get("nodes") or [])
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return nodes
        next_cursor = page_info.get("endCursor")
        if not next_cursor or next_cursor in seen_cursors or next_cursor == cursor:
            raise ShopifyCliError(f"pagination of {'.'.join(path)} returned a repeated or empty cursor")
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    raise ShopifyCliError(f"pagination of {'.'.join(path)} exceeded {MAX_PAGES} pages")


def complete_connection(run: Runner, connection: dict, query: str, path: list[str],
                        parent_id: str) -> list[dict]:
    """Return all nodes of a nested connection, fetching the pages after the first if needed.

    ``query`` reads the same connection through ``node(id: $id)`` and takes ``$after``.
    """
    nodes = list(connection.get("nodes") or [])
    page_info = connection.get("pageInfo") or {}
    if not page_info.get("hasNextPage"):
        return nodes
    return nodes + paginate(run, query, path, variables={"id": parent_id},
                            after=page_info.get("endCursor"))


class ShopifyCliRunner:
    """Runs read-only GraphQL through `shopify store execute` against one store."""

    def __init__(self, store: str, executable: Optional[str] = None, timeout: int = 180) -> None:
        self.store = store
        self.executable = executable or shutil.which("shopify")
        if not self.executable:
            raise ShopifyCliError("the `shopify` CLI is not on PATH")
        self.timeout = timeout

    def __call__(self, query: str, variables: dict) -> dict:
        assert_read_only(query)
        # A neutral cwd: the CLI must not pick up a project or theme config from the repo.
        with tempfile.TemporaryDirectory(prefix="rosart-export-") as tmp:
            tmp_path = Path(tmp)
            query_file = tmp_path / "query.graphql"
            variables_file = tmp_path / "variables.json"
            query_file.write_text(query, encoding="utf-8")
            variables_file.write_text(json.dumps(variables), encoding="utf-8")
            command = [self.executable, "store", "execute", "--store", self.store,
                       "--no-input", "--no-color", "--json",
                       "--query-file", str(query_file), "--variable-file", str(variables_file)]
            try:
                completed = subprocess.run(command, cwd=tmp, capture_output=True, text=True,
                                           encoding="utf-8", errors="replace", timeout=self.timeout)
            except subprocess.TimeoutExpired as exc:
                raise ShopifyCliError(f"shopify store execute timed out after {self.timeout}s") from exc
        try:
            return parse_cli_output(completed.stdout)
        except ShopifyCliError as exc:
            if completed.returncode != 0 and "no result document" in str(exc):
                raise ShopifyCliError(
                    f"shopify store execute exited {completed.returncode}: {completed.stderr.strip()[-500:]}"
                ) from exc
            raise


def _is_mutation(query: str) -> bool:
    try:
        assert_read_only(query)
    except ShopifyCliError:
        return True
    return False


def _unwrap(result: dict) -> dict:
    """Accept both the bare data document and a GraphQL ``{"data", "errors"}`` envelope."""
    if "data" in result and set(result) <= {"data", "errors", "extensions"}:
        if result.get("errors"):
            messages = "; ".join(str(e.get("message", e)) if isinstance(e, dict) else str(e)
                                 for e in result["errors"])
            raise ShopifyCliError(f"GraphQL errors: {messages}")
        if not isinstance(result["data"], dict):
            raise ShopifyCliError("the GraphQL response has no data")
        return result["data"]
    return result


class ShopifyCliFileRunner:
    """Runs GraphQL through `shopify store execute` with files only, never a stdout pipe.

    Windows encoding rule (a pipe once corrupted a live page): variables go in a file written as
    ASCII-escaped JSON, the result comes back through ``--output-file`` and is decoded as strict
    UTF-8, and stdout/stderr go to files that are read only to explain a failure. The CLI runs
    from a fresh temporary directory so it never picks up a project config. Mutations are refused
    locally unless ``allow_mutations`` is set, and only a mutation gets ``--allow-mutations``.
    """

    def __init__(self, store: str, executable: Optional[str] = None, timeout: int = 180,
                 allow_mutations: bool = False, api_version: Optional[str] = None,
                 run_process: Callable[..., Any] = subprocess.run) -> None:
        self.store = store
        self.executable = executable or shutil.which("shopify")
        if not self.executable:
            raise ShopifyCliError("the `shopify` CLI is not on PATH")
        self.timeout = timeout
        self.allow_mutations = allow_mutations
        self.api_version = api_version
        self.run_process = run_process

    def __call__(self, query: str, variables: dict) -> dict:
        mutation = _is_mutation(query)
        if mutation and not self.allow_mutations:
            raise ShopifyCliError("refusing to run a mutation: this runner was not opened for writes")
        with tempfile.TemporaryDirectory(prefix="rosart-cli-") as tmp:
            tmp_path = Path(tmp)
            query_file = tmp_path / "query.graphql"
            variables_file = tmp_path / "variables.json"
            output_file = tmp_path / "result.json"
            query_file.write_bytes(query.encode("utf-8"))
            variables_file.write_bytes(json.dumps(variables, ensure_ascii=True).encode("ascii"))
            command = [self.executable, "store", "execute", "--store", self.store,
                       "--no-input", "--no-color", "--json",
                       "--query-file", str(query_file), "--variable-file", str(variables_file),
                       "--output-file", str(output_file)]
            if self.api_version:
                command += ["--version", self.api_version]
            if mutation:
                command.append("--allow-mutations")
            with (tmp_path / "stdout.txt").open("wb") as stdout, (tmp_path / "stderr.txt").open("wb") as stderr:
                try:
                    completed = self.run_process(command, cwd=tmp, stdout=stdout, stderr=stderr,
                                                 stdin=subprocess.DEVNULL, timeout=self.timeout)
                except subprocess.TimeoutExpired as exc:
                    raise ShopifyCliError(f"shopify store execute timed out after {self.timeout}s") from exc
            console = ((tmp_path / "stdout.txt").read_bytes().decode("utf-8", errors="replace")
                       + (tmp_path / "stderr.txt").read_bytes().decode("utf-8", errors="replace"))
            raw = output_file.read_bytes() if output_file.exists() else b""
            if completed.returncode != 0 or not raw.strip():
                try:
                    parse_cli_output(console)
                except ShopifyCliError as exc:
                    if "no result document" not in str(exc):
                        raise
                raise ShopifyCliError(f"shopify store execute exited {completed.returncode} "
                                      f"without a result: {console.strip()[-500:]}")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ShopifyCliError(f"the CLI output file is not valid UTF-8 ({exc}); refusing to guess") from exc
        return _unwrap(parse_cli_output(text))
