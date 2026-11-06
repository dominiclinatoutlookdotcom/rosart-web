"""Applies an owner-approved SEO plan (seo/plan.json) to the Rosart Shopify store.

The owner's listing checklist, section 3 ("Writing in bulk"), step by step:
1. Approval gate: the plan file's sha256 must equal --approved (one changed byte refuses), the
   plan must be for this store, and tools/seo_check.py must pass on exactly those bytes.
2. Scope check: currentAppInstallation.accessScopes must hold every scope a planned change
   needs (write_products, write_files, write_content); a --write run refuses and names the gaps.
3. Drift check: each batch re-reads its targets first. A field whose live value is not the plan's
   `before` is skipped and reported, never overwritten (a live value already equal to `after`
   is skipped as already applied, so a stopped run can simply be re-run).
4. Writes per the plan's mechanism (productUpdate, collectionUpdate, pageUpdate metafields,
   one fileUpdate per batch for alt text), in batches of 10 with a pause between batches.
   `manual` entries (the homepage) are only listed. A null payload, a missing object or
   userErrors is a failure: Shopify can refuse silently.
5. Rollback log: the exact live value of each field is appended to seo/apply-log-<UTC>.jsonl
   and synced to disk before the mutation that changes it. --rollback <log> restores them, with
   the same drift check against the value the apply wrote.
6. Read-back: after each batch every written field (and an SEO field sent unchanged beside it)
   is re-read and compared whole with `after`; the run stops after the first batch that fails
   or mismatches.
7. The CLI is driven through files only (shopify_cli.ShopifyCliFileRunner): ASCII-escaped
   variable file, UTF-8 --output-file, a temporary cwd, --allow-mutations only on a --write run.
8. The dry run is the default: it prints the changes per field, the mutation calls, the scope
   check and the drift check (reads only) and writes nothing.

Usage:
  python tools/seo_apply.py --plan seo/plan.json [--approved <sha256>]           (dry run)
  python tools/seo_apply.py --plan seo/plan.json --approved <sha256> --write
  python tools/seo_apply.py --rollback seo/apply-log-<UTC>.jsonl [--write]
Exit status: 0 clean; 1 refused, or any drift, failure or mismatch (see the printed report).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from seo_check import check_plan
from shopify_cli import ShopifyCliError, ShopifyCliFileRunner

ROOT = Path(__file__).resolve().parent.parent
STORE = "rosart-mhfmyuxq.myshopify.com"
API_VERSION = "2026-10"  # the version the plan's mechanisms and these operations were checked against
DEFAULT_BATCH_SIZE = 10
READ_CHUNK = 50

Runner = Callable[[str, dict], dict]
RunnerFactory = Callable[[bool], Runner]

SUPPORTED = {
    ("page", "seo.title"), ("page", "seo.description"),
    ("collection", "descriptionHtml"), ("collection", "seo.title"), ("collection", "seo.description"),
    ("collection", "image.altText"),
    ("product", "seo.title"), ("product", "seo.description"), ("product", "category"),
    ("product", "vendor"), ("product", "productType"),
    ("product_media", "alt"), ("file", "alt"),
}
FILE_RESOURCES = ("product_media", "file")
PAGE_METAFIELD_KEYS = {"seo.title": "title_tag", "seo.description": "description_tag"}

SCOPES_QUERY = "query SeoApplyScopes { currentAppInstallation { accessScopes { handle } } }"
READ_QUERY = """query SeoApplyRead($ids: [ID!]!) {
  nodes(ids: $ids) {
    __typename
    id
    ... on Product { handle vendor productType seo { title description } category { id } }
    ... on Collection { handle descriptionHtml seo { title description } image { id altText } }
    ... on Page {
      handle
      titleTag: metafield(namespace: "global", key: "title_tag") { value }
      descriptionTag: metafield(namespace: "global", key: "description_tag") { value }
    }
    ... on MediaImage { alt }
  }
}"""
PRODUCT_UPDATE = """mutation SeoApplyProduct($product: ProductUpdateInput!) {
  productUpdate(product: $product) { product { id } userErrors { field message } }
}"""
COLLECTION_UPDATE = """mutation SeoApplyCollection($collection: CollectionUpdateInput!) {
  collectionUpdate(collection: $collection) { collection { id } userErrors { field message } }
}"""
PAGE_UPDATE = """mutation SeoApplyPage($id: ID!, $page: PageUpdateInput!) {
  pageUpdate(id: $id, page: $page) { page { id } userErrors { field message } }
}"""
METAFIELDS_DELETE = """mutation SeoApplyMetafieldsDelete($metafields: [MetafieldIdentifierInput!]!) {
  metafieldsDelete(metafields: $metafields) { deletedMetafields { ownerId namespace key } userErrors { field message } }
}"""
FILE_UPDATE = """mutation SeoApplyFiles($files: [FileUpdateInput!]!) {
  fileUpdate(files: $files) { files { id alt } userErrors { field message code } }
}"""


class ApplyError(RuntimeError):
    """The run was refused before anything was written."""


# ---------------------------------------------------------------- loading and gating the plan

def load_approved_plan(path: Path, approved: Optional[str], require_approval: bool) -> tuple[dict[str, Any], str]:
    """The plan, parsed from exactly the bytes whose sha256 was checked against the approval."""
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if approved is None and require_approval:
        raise ApplyError("a --write run needs --approved <sha256 of the plan file the owner approved>")
    if approved is not None and approved.strip().lower() != digest:
        raise ApplyError(f"plan hash {digest} does not match the approved hash {approved}")
    plan = json.loads(data.decode("utf-8"))
    if plan.get("store") != STORE:
        raise ApplyError(f"plan is for store {plan.get('store')!r}, this tool only writes {STORE}")
    errors = check_plan(plan)
    if errors:
        raise ApplyError("seo_check found errors:\n  " + "\n  ".join(errors))
    for entry in plan.get("changes") or []:
        if not is_manual(entry) and (entry["resource"], entry["field"]) not in SUPPORTED:
            raise ApplyError(f"no writer for {entry['resource']} {entry['field']} ({entry['id']})")
    return plan, digest


def is_manual(entry: dict[str, Any]) -> bool:
    return not entry.get("scope") or str(entry.get("mechanism", "")).startswith("manual")


def required_scopes(entries: list[dict[str, Any]]) -> list[str]:
    return sorted({e["scope"] for e in entries if not is_manual(e)})


def missing_scopes(run: Runner, entries: list[dict[str, Any]]) -> list[str]:
    data = run(SCOPES_QUERY, {})
    granted = {s.get("handle") for s in ((data.get("currentAppInstallation") or {}).get("accessScopes") or [])}
    return [s for s in required_scopes(entries) if s not in granted]


# ---------------------------------------------------------------- operations and live values

@dataclass
class Op:
    """One planned field write: `before` is the value the plan expects to find live."""
    entry: dict[str, Any]
    target: str            # the node read and mutated (the collection for a collection image)
    status: str = "pending"
    live: Any = None       # normalised live value, compared with the plan
    raw: Any = None        # exact live value as read, logged for the rollback
    detail: str = ""

    @property
    def id(self) -> str:
        return self.entry["id"]

    @property
    def resource(self) -> str:
        return self.entry["resource"]

    @property
    def field(self) -> str:
        return self.entry["field"]

    @property
    def before(self) -> Any:
        return normalise(self.field, self.entry["before"])

    @property
    def after(self) -> Any:
        return normalise(self.field, self.entry["after"])


def normalise(field_name: str, value: Any) -> Any:
    """Shopify returns null for an unset text field; the plan records it as ""."""
    if field_name == "category":
        return value or None
    return "" if value is None else value


class Unreadable(str):
    """A live value that cannot be compared (missing node, replaced image); never equals a plan value."""

    def __eq__(self, other: object) -> bool:
        return False

    def __hash__(self) -> int:
        return id(self)


def live_value(node: Optional[dict[str, Any]], op: Op) -> Any:
    raw = raw_value(node, op)
    return raw if isinstance(raw, Unreadable) else normalise(op.field, raw)


def raw_value(node: Optional[dict[str, Any]], op: Op) -> Any:
    """The field exactly as Shopify returned it (null stays None)."""
    if node is None:
        return Unreadable(f"<{op.target} not found>")
    resource, name = op.resource, op.field
    if name in ("seo.title", "seo.description") and resource != "page":
        value = (node.get("seo") or {}).get(name.split(".")[1])
    elif resource == "page":
        alias = "titleTag" if name == "seo.title" else "descriptionTag"
        value = (node.get(alias) or {}).get("value")
    elif name == "category":
        value = (node.get("category") or {}).get("id")
    elif name == "image.altText":
        image = node.get("image")
        if not image or image.get("id") != op.entry["gid"]:
            return Unreadable(f"<collection image is now {image.get('id') if image else 'none'}>")
        value = image.get("altText")
    else:
        value = node.get(name)
    return value


def build_ops(entries: list[dict[str, Any]], all_entries: list[dict[str, Any]]) -> list[Op]:
    collection_ids = {e["handle"]: e["gid"] for e in all_entries
                      if e.get("resource") == "collection" and str(e.get("gid", "")).startswith("gid://shopify/Collection/")}
    ops = []
    for entry in entries:
        target = entry["gid"]
        if entry["field"] == "image.altText":
            target = entry.get("target") or collection_ids.get(entry["handle"])
            if not target:
                raise ApplyError(f"{entry['id']}: no collection gid for handle {entry['handle']!r} in the plan")
        ops.append(Op(entry=entry, target=target))
    return ops


def read_nodes(run: Runner, ids: list[str]) -> dict[str, Optional[dict[str, Any]]]:
    unique = list(dict.fromkeys(ids))
    found: dict[str, Optional[dict[str, Any]]] = {}
    for start in range(0, len(unique), READ_CHUNK):
        chunk = unique[start:start + READ_CHUNK]
        nodes = run(READ_QUERY, {"ids": chunk}).get("nodes") or []
        for gid, node in zip(chunk, nodes + [None] * (len(chunk) - len(nodes))):
            found[gid] = node
    return found


def classify(ops: list[Op], nodes: dict[str, Optional[dict[str, Any]]]) -> None:
    """ready: live equals the plan's before; done: live already equals after; drift: anything else."""
    for op in ops:
        op.raw = raw_value(nodes.get(op.target), op)
        op.live = live_value(nodes.get(op.target), op)
        if op.live == op.before:
            op.status = "ready"
        elif op.live == op.after:
            op.status = "done"
        else:
            op.status = "drift"
            op.detail = f"live {op.live!r} differs from the plan's before {op.before!r}"


# ---------------------------------------------------------------- units, batches and mutations

def units(ops: list[Op]) -> list[list[Op]]:
    """One unit per mutated node (a product, collection or page); each file is its own unit."""
    grouped: dict[tuple[str, str], list[Op]] = {}
    for op in ops:
        key = ("file", op.target + "|" + op.id) if op.resource in FILE_RESOURCES else (op.resource, op.target)
        grouped.setdefault(key, []).append(op)
    return list(grouped.values())


def batches(ops: list[Op], size: int) -> list[list[list[Op]]]:
    all_units = units(ops)
    return [all_units[i:i + size] for i in range(0, len(all_units), size)]


def call_names(batch: list[list[Op]]) -> list[str]:
    names = []
    for unit in batch:
        resource = unit[0].resource
        if resource in FILE_RESOURCES:
            continue
        names.append({"product": "productUpdate", "collection": "collectionUpdate", "page": "pageUpdate"}[resource])
    if any(unit[0].resource in FILE_RESOURCES for unit in batch):
        names.append("fileUpdate")
    return names


def _seo_input(unit: list[Op], node: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Both SEO fields, always: the planned value, else the live one, so nothing is cleared by omission."""
    seo_ops = {op.field.split(".")[1]: op for op in unit if op.field in ("seo.title", "seo.description")}
    if not seo_ops:
        return None
    live = (node or {}).get("seo") or {}
    return {key: (seo_ops[key].entry["after"] if key in seo_ops else live.get(key))
            for key in ("title", "description")}


def _payload_error(data: dict[str, Any], name: str, object_key: str) -> Optional[str]:
    payload = data.get(name)
    if payload is None:
        return f"{name} returned a null payload (silent refusal)"
    errors = payload.get("userErrors") or []
    if errors:
        return f"{name} userErrors: " + "; ".join(f"{e.get('field')}: {e.get('message')}" for e in errors)
    if object_key and payload.get(object_key) is None:
        return f"{name} returned no {object_key} and no userErrors (silent refusal)"
    return None


def write_unit(run: Runner, unit: list[Op], node: Optional[dict[str, Any]]) -> Optional[str]:
    """Send the mutation for one product, collection or page; returns an error message or None."""
    resource, target = unit[0].resource, unit[0].target
    if resource == "product":
        product: dict[str, Any] = {"id": target}
        seo = _seo_input(unit, node)
        if seo is not None:
            product["seo"] = seo
        for op in unit:
            if op.field in ("category", "vendor", "productType"):
                product[op.field] = op.entry["after"]
        return _payload_error(run(PRODUCT_UPDATE, {"product": product}), "productUpdate", "product")
    if resource == "collection":
        collection: dict[str, Any] = {"id": target}
        seo = _seo_input(unit, node)
        if seo is not None:
            collection["seo"] = seo
        for op in unit:
            if op.field == "descriptionHtml":
                collection["descriptionHtml"] = op.entry["after"]
            elif op.field == "image.altText":
                collection["image"] = {"id": op.entry["gid"], "altText": op.entry["after"]}
        return _payload_error(run(COLLECTION_UPDATE, {"collection": collection}), "collectionUpdate", "collection")
    if resource == "page":
        sets = [op for op in unit if op.entry["after"] not in ("", None)]
        clears = [op for op in unit if op.entry["after"] in ("", None)]
        if sets:
            metafields = [{"namespace": "global", "key": PAGE_METAFIELD_KEYS[op.field],
                           "type": "single_line_text_field", "value": op.entry["after"]} for op in sets]
            error = _payload_error(run(PAGE_UPDATE, {"id": target, "page": {"metafields": metafields}}),
                                   "pageUpdate", "page")
            if error:
                return error
        if clears:
            # A blank metafield value is refused; restoring "no value" means deleting the metafield.
            identifiers = [{"ownerId": target, "namespace": "global", "key": PAGE_METAFIELD_KEYS[op.field]}
                           for op in clears]
            data = run(METAFIELDS_DELETE, {"metafields": identifiers})
            error = _payload_error(data, "metafieldsDelete", "deletedMetafields")
            if error:
                return error
            if any(d is None for d in data["metafieldsDelete"]["deletedMetafields"]):
                return "metafieldsDelete returned a null entry (silent refusal)"
        return None
    raise ApplyError(f"no writer for {resource}")


def write_files(run: Runner, file_ops: list[Op]) -> dict[str, Optional[str]]:
    """One fileUpdate for every file in the batch; returns an error (or None) per op id."""
    data = run(FILE_UPDATE, {"files": [{"id": op.target, "alt": op.entry["after"]} for op in file_ops]})
    error = _payload_error(data, "fileUpdate", "files")
    if error:
        return {op.id: error for op in file_ops}
    returned = {f.get("id") for f in data["fileUpdate"]["files"] if f}
    return {op.id: None if op.target in returned else "fileUpdate did not return this file (silent refusal)"
            for op in file_ops}


# ---------------------------------------------------------------- the rollback log

class ApplyLog:
    """Append-only JSON lines: the exact live value of every field, written and synced to disk
    before the mutation that changes it is sent. `--rollback` replays these lines."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def _append(self, records: list[dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def before(self, ops: list[Op]) -> None:
        at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self._append([{"type": "before", "id": op.id, "resource": op.resource, "gid": op.entry["gid"],
                       "handle": op.entry["handle"], "field": op.field, "target": op.target,
                       "scope": op.entry["scope"], "mechanism": op.entry["mechanism"],
                       "before": op.raw, "after": op.entry["after"], "at": at} for op in ops])

    def results(self, ops: list[Op]) -> None:
        if ops:
            self._append([{"type": "result", "id": op.id, "status": op.status, "detail": op.detail}
                          for op in ops])


def load_rollback(path: Path) -> list[dict[str, Any]]:
    """Change entries that put back every value a log recorded: the logged live value becomes
    the target, and the value the apply wrote is what must still be live (the drift check)."""
    entries: dict[str, dict[str, Any]] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ApplyError(f"{path}:{number} is not JSON: {exc}") from exc
        if record.get("type") != "before" or record["id"] in entries:
            continue  # the first logged value of a field is the one from before any write
        entries[record["id"]] = {
            "id": record["id"], "resource": record["resource"], "gid": record["gid"],
            "handle": record["handle"], "field": record["field"], "target": record["target"],
            "scope": record["scope"], "mechanism": record["mechanism"],
            "before": record["after"], "after": record["before"],
        }
    if not entries:
        raise ApplyError(f"{path} records no values to restore")
    for entry in entries.values():
        if (entry["resource"], entry["field"]) not in SUPPORTED:
            raise ApplyError(f"no writer for {entry['resource']} {entry['field']} ({entry['id']})")
    return list(entries.values())


def new_log_path(log_dir: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = log_dir / f"apply-log-{stamp}.jsonl"
    counter = 1
    while path.exists():
        counter += 1
        path = log_dir / f"apply-log-{stamp}-{counter}.jsonl"
    return path


# ---------------------------------------------------------------- read-back

def _sibling_checks(unit: list[Op], node: Optional[dict[str, Any]]) -> list[Op]:
    """The SEO field sent unchanged alongside a planned one: it must read back as it was."""
    planned = {op.field for op in unit}
    checks = []
    if unit[0].resource in ("product", "collection") and planned & {"seo.title", "seo.description"}:
        for name in ("seo.title", "seo.description"):
            if name not in planned:
                current = ((node or {}).get("seo") or {}).get(name.split(".")[1])
                base = unit[0].entry["id"].rsplit("/", 1)[0]
                entry = dict(unit[0].entry, id=f"{base}/{name} (sent unchanged)", field=name,
                             before=current, after=current)
                checks.append(Op(entry=entry, target=unit[0].target, status="written"))
    return checks


def read_back(run: Runner, written: list[Op]) -> None:
    """Re-read every written field; the whole value must equal the plan's after exactly."""
    if not written:
        return
    nodes = read_nodes(run, [op.target for op in written])
    for op in written:
        value = live_value(nodes.get(op.target), op)
        if value == op.after:
            op.status = "verified"
        else:
            op.status = "mismatch"
            op.detail = f"read back {value!r}, expected {op.after!r}"


# ---------------------------------------------------------------- the run

@dataclass
class Report:
    ops: list[Op] = field(default_factory=list)
    manual: list[dict[str, Any]] = field(default_factory=list)
    stopped: bool = False

    def with_status(self, status: str) -> list[Op]:
        return [op for op in self.ops if op.status == status]

    @property
    def clean(self) -> bool:
        return not self.stopped and not any(self.with_status(s) for s in ("drift", "failed", "mismatch"))


def apply_ops(run: Runner, ops: list[Op], log: ApplyLog, *, batch_size: int, pause: float,
              sleep: Callable[[float], None], out: Callable[[str], None]) -> tuple[bool, list[Op]]:
    """Write in batches, each one re-read for drift first, logged, written, then read back.
    Returns (finished, sibling checks that failed); stops after the first batch with a failure
    or a read-back mismatch."""
    planned = batches(ops, batch_size)
    bad_siblings: list[Op] = []
    for index, batch in enumerate(planned, start=1):
        batch_ops = [op for unit in batch for op in unit]
        nodes = read_nodes(run, [op.target for op in batch_ops])
        classify(batch_ops, nodes)
        file_ops: list[Op] = []
        checks: list[Op] = []
        for unit in batch:
            ready = [op for op in unit if op.status == "ready"]
            if not ready:
                continue
            if ready[0].resource in FILE_RESOURCES:
                file_ops.extend(ready)
                continue
            log.before(ready)
            error = write_unit(run, ready, nodes.get(ready[0].target))
            for op in ready:
                op.status, op.detail = ("failed", error) if error else ("written", "")
            if not error:
                checks.extend(_sibling_checks(ready, nodes.get(ready[0].target)))
        if file_ops:
            log.before(file_ops)
            for op_id, error in write_files(run, file_ops).items():
                op = next(o for o in file_ops if o.id == op_id)
                op.status, op.detail = ("failed", error) if error else ("written", "")
        read_back(run, [op for op in batch_ops if op.status == "written"] + checks)
        bad_siblings.extend(op for op in checks if op.status == "mismatch")
        log.results([op for op in batch_ops if op.status in ("verified", "mismatch", "failed")])
        out(f"batch {index}/{len(planned)}: " + ", ".join(
            f"{s} {n}" for s in ("verified", "done", "drift", "failed", "mismatch")
            if (n := sum(1 for op in batch_ops + checks if op.status == s))))
        if any(op.status in ("failed", "mismatch") for op in batch_ops + checks):
            out(f"STOPPED after batch {index}: a write failed or did not read back as planned")
            return False, bad_siblings
        if index < len(planned):
            sleep(pause)
    return True, bad_siblings


def _value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def print_report(report: Report, out: Callable[[str], None]) -> None:
    for op in report.ops:
        if op.status == "drift":
            out(f"DRIFT {op.id}: skipped, {op.detail}")
        elif op.status == "failed":
            out(f"FAILED {op.id}: {op.detail}")
        elif op.status == "mismatch":
            out(f"MISMATCH {op.id}: {op.detail}")
    for entry in report.manual:
        out(f"MANUAL {entry['id']}: set by hand: {entry['mechanism']}\n    value: {_value(entry['after'])}")


def dry_run(plan: dict[str, Any], digest: str, run: Runner, batch_size: int, out: Callable[[str], None],
            source: str = "Plan") -> int:
    changes = plan.get("changes") or []
    writable = [e for e in changes if not is_manual(e)]
    ops = build_ops(writable, changes + (plan.get("kept") or []))
    out(f"DRY RUN: nothing is written. {source} sha256 {digest}")
    if source == "Plan":
        out(f"  approve this exact file with: --approved {digest}")
    out("Changes a --write run would make, per field:")
    counts: dict[str, int] = {}
    for op in ops:
        key = f"{op.resource} {op.field}"
        counts[key] = counts.get(key, 0) + 1
    for key, count in counts.items():
        out(f"  {key}: {count}")
    planned = batches(ops, batch_size)
    calls: dict[str, int] = {}
    for batch in planned:
        for name in call_names(batch):
            calls[name] = calls.get(name, 0) + 1
    out(f"Mutation calls ({len(planned)} batch(es) of up to {batch_size}): "
        + ", ".join(f"{name} x{n}" for name, n in sorted(calls.items())))
    missing = missing_scopes(run, writable)
    out(f"Scopes needed: {', '.join(required_scopes(writable)) or 'none'}; "
        f"missing: {', '.join(missing) or 'none'}")
    nodes = read_nodes(run, [op.target for op in ops])
    classify(ops, nodes)
    report = Report(ops=ops, manual=[e for e in changes if is_manual(e)])
    print_report(report, out)
    ready = len(report.with_status("ready"))
    out(f"{ready} change(s) ready, {len(report.with_status('done'))} already applied, "
        f"{len(report.with_status('drift'))} drifted, {len(report.manual)} manual")
    if missing:
        out(f"NOT READY: missing scopes {', '.join(missing)}; a --write run would refuse")
    return 0 if not missing and report.clean else 1


def write_run(plan: dict[str, Any], run: Runner, log: ApplyLog, *, batch_size: int, pause: float,
              sleep: Callable[[float], None], out: Callable[[str], None]) -> int:
    changes = plan.get("changes") or []
    writable = [e for e in changes if not is_manual(e)]
    ops = build_ops(writable, changes + (plan.get("kept") or []))
    missing = missing_scopes(run, writable)
    if missing:
        out(f"REFUSED: the CLI login lacks scopes {', '.join(missing)} (needed by this plan); nothing written")
        return 1
    report = Report(ops=ops, manual=[e for e in changes if is_manual(e)])
    finished, bad_siblings = apply_ops(run, ops, log, batch_size=batch_size, pause=pause, sleep=sleep, out=out)
    report.stopped = not finished
    report.ops.extend(bad_siblings)
    print_report(report, out)
    if log.path.exists():
        out(f"rollback log: {log.path}")
    out(f"written and verified {len(report.with_status('verified'))}, "
        f"mismatched {len(report.with_status('mismatch'))}, already applied {len(report.with_status('done'))}, "
        f"drift skipped {len(report.with_status('drift'))}, failed {len(report.with_status('failed'))}, "
        f"not reached {len(report.with_status('pending'))}, manual {len(report.manual)}")
    return 0 if report.clean else 1


def cli_runner_factory(allow_mutations: bool) -> Runner:
    """The real store: mutations are possible only for a --write run."""
    return ShopifyCliFileRunner(STORE, allow_mutations=allow_mutations, api_version=API_VERSION)


def main(argv: Optional[list[str]] = None, runner_factory: Optional[RunnerFactory] = None,
         sleep: Callable[[float], None] = time.sleep) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--plan", type=Path, default=ROOT / "seo" / "plan.json")
    parser.add_argument("--approved", help="sha256 of the plan file the owner approved")
    parser.add_argument("--rollback", type=Path, metavar="LOG",
                        help="restore the values an apply log recorded (instead of applying a plan)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="the default: read, compare and report only")
    mode.add_argument("--write", action="store_true", help="write to the store")
    parser.add_argument("--log-dir", type=Path, default=ROOT / "seo")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--pause", type=float, default=2.0, help="seconds between batches")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.batch_size < 1:
        parser.error("--batch-size must be at least 1")
    log = ApplyLog(new_log_path(args.log_dir))
    try:
        if args.rollback:
            plan = {"changes": load_rollback(args.rollback)}
            digest = hashlib.sha256(args.rollback.read_bytes()).hexdigest()
            source = f"ROLLBACK of {args.rollback}; log"
        else:
            plan, digest = load_approved_plan(args.plan, args.approved, require_approval=args.write)
            source = "Plan"
        factory = runner_factory or cli_runner_factory
        run = factory(args.write)
        if not args.write:
            return dry_run(plan, digest, run, args.batch_size, print, source=source)
        return write_run(plan, run, log, batch_size=args.batch_size, pause=args.pause, sleep=sleep, out=print)
    except ApplyError as exc:
        print(f"REFUSED: {exc}")
        return 1
    except ShopifyCliError as exc:
        print(f"STOPPED: {exc}")
        if log.path.exists():
            print(f"rollback log (values from before any write): {log.path}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
