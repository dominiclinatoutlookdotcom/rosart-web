"""An in-memory Shopify Admin store for the SEO apply tests: answers the reads and mutations
`tools/seo_apply.py` sends, records every call, and can be told to misbehave (refuse silently,
store a different value, lack a scope). It never touches the network."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
from typing import Any, Optional

ALL_SCOPES = ["read_content", "read_files", "read_products", "write_content", "write_files", "write_products"]


def _op(query: str) -> str:
    for name in ("metafieldsDelete", "productUpdate", "collectionUpdate", "pageUpdate", "fileUpdate",
                 "currentAppInstallation", "nodes"):
        if re.search(rf"\b{name}\b", query):
            return name
    raise AssertionError(f"fake store does not understand: {query[:80]}")


class FakeStore:
    """`store(query, variables) -> data`, the same runner seam the real CLI wrapper offers."""

    def __init__(self, scopes: Optional[list[str]] = None) -> None:
        self.scopes = list(ALL_SCOPES if scopes is None else scopes)
        self.nodes: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, dict]] = []
        self.silent_null: set[str] = set()      # gids whose mutation returns a null payload
        self.user_errors: dict[str, str] = {}   # gid -> userErrors message
        self.mangle: dict[str, str] = {}        # gid -> value stored instead of the written one
        self.on_mutation = None                 # callback(op, variables) run before a mutation

    # ------------------------------------------------------------ seeding
    def add_product(self, gid, handle, seo_title="", seo_description="", category=None, vendor="Rosart",
                    product_type="Facial Toner"):
        self.nodes[gid] = {"__typename": "Product", "id": gid, "handle": handle, "vendor": vendor,
                           "productType": product_type,
                           "seo": {"title": seo_title or None, "description": seo_description or None},
                           "category": {"id": category} if category else None}

    def add_collection(self, gid, handle, description_html="", seo_title="", seo_description="",
                       image_id=None, image_alt=""):
        self.nodes[gid] = {"__typename": "Collection", "id": gid, "handle": handle,
                           "descriptionHtml": description_html,
                           "seo": {"title": seo_title or None, "description": seo_description or None},
                           "image": {"id": image_id, "altText": image_alt or None} if image_id else None}

    def add_page(self, gid, handle, title_tag=None, description_tag=None):
        self.nodes[gid] = {"__typename": "Page", "id": gid, "handle": handle,
                           "metafields": {"title_tag": title_tag, "description_tag": description_tag}}

    def add_file(self, gid, alt=""):
        self.nodes[gid] = {"__typename": "MediaImage", "id": gid, "alt": alt}

    # ------------------------------------------------------------ inspection
    def mutations(self) -> list[tuple[str, dict]]:
        return [(q, v) for q, v in self.calls if q.lstrip().startswith("mutation")]

    def value(self, gid: str, field: str) -> Any:
        node = self.nodes[gid]
        if field in ("seo.title", "seo.description"):
            return node["seo"][field.split(".")[1]]
        if field == "category":
            return (node["category"] or {}).get("id")
        if field == "image.altText":
            return node["image"]["altText"]
        if field in ("title_tag", "description_tag"):
            return node["metafields"][field]
        return node[field]

    # ------------------------------------------------------------ the runner seam
    def __call__(self, query: str, variables: dict) -> dict:
        self.calls.append((query, copy.deepcopy(variables)))
        op = _op(query)
        if op == "currentAppInstallation":
            return {"currentAppInstallation": {"accessScopes": [{"handle": s} for s in self.scopes]}}
        if op == "nodes":
            return {"nodes": [self._read(gid) for gid in variables["ids"]]}
        if self.on_mutation:
            self.on_mutation(op, variables)
        return getattr(self, f"_{op}")(variables)

    def _read(self, gid: str) -> Optional[dict]:
        node = self.nodes.get(gid)
        if node is None:
            return None
        out = {k: copy.deepcopy(v) for k, v in node.items() if k != "metafields"}
        if node["__typename"] == "Page":
            for alias, key in (("titleTag", "title_tag"), ("descriptionTag", "description_tag")):
                value = node["metafields"][key]
                out[alias] = {"value": value} if value is not None else None
        return out

    def _refusal(self, gid: str, name: str, obj_key: str) -> Optional[dict]:
        if gid in self.silent_null:
            return {name: {obj_key: None, "userErrors": []}}
        if gid in self.user_errors:
            return {name: {obj_key: None, "userErrors": [{"field": ["id"], "message": self.user_errors[gid]}]}}
        return None

    def _stored(self, gid: str, value: Any) -> Any:
        return self.mangle.get(gid, value)

    def _productUpdate(self, v: dict) -> dict:
        data = v["product"]
        gid = data["id"]
        refused = self._refusal(gid, "productUpdate", "product")
        if refused:
            return refused
        node = self.nodes[gid]
        if "seo" in data:
            for key in ("title", "description"):
                if key in data["seo"]:
                    node["seo"][key] = self._stored(gid, data["seo"][key])
        if "category" in data:
            node["category"] = {"id": data["category"]} if data["category"] else None
        for key in ("vendor", "productType"):
            if key in data:
                node[key] = self._stored(gid, data[key])
        return {"productUpdate": {"product": self._read(gid), "userErrors": []}}

    def _collectionUpdate(self, v: dict) -> dict:
        data = v["collection"]
        gid = data["id"]
        refused = self._refusal(gid, "collectionUpdate", "collection")
        if refused:
            return refused
        node = self.nodes[gid]
        if "descriptionHtml" in data:
            node["descriptionHtml"] = self._stored(gid, data["descriptionHtml"])
        if "seo" in data:
            for key in ("title", "description"):
                if key in data["seo"]:
                    node["seo"][key] = self._stored(gid, data["seo"][key])
        if "image" in data:
            assert data["image"]["id"] == node["image"]["id"], "image id must be the current image"
            node["image"]["altText"] = self._stored(gid, data["image"]["altText"])
        return {"collectionUpdate": {"collection": self._read(gid), "userErrors": []}}

    def _pageUpdate(self, v: dict) -> dict:
        gid = v["id"]
        refused = self._refusal(gid, "pageUpdate", "page")
        if refused:
            return refused
        for field in v["page"]["metafields"]:
            assert field["namespace"] == "global" and field["type"] == "single_line_text_field"
            assert field["value"] != "", "Shopify refuses a blank metafield value"
            self.nodes[gid]["metafields"][field["key"]] = self._stored(gid, field["value"])
        return {"pageUpdate": {"page": self._read(gid), "userErrors": []}}

    def _metafieldsDelete(self, v: dict) -> dict:
        deleted = []
        for ident in v["metafields"]:
            gid = ident["ownerId"]
            if gid in self.silent_null:
                deleted.append(None)
                continue
            self.nodes[gid]["metafields"][ident["key"]] = None
            deleted.append({"ownerId": gid, "namespace": ident["namespace"], "key": ident["key"]})
        return {"metafieldsDelete": {"deletedMetafields": deleted, "userErrors": []}}

    def _fileUpdate(self, v: dict) -> dict:
        files = []
        errors = []
        for item in v["files"]:
            gid = item["id"]
            if gid in self.silent_null:
                continue
            if gid in self.user_errors:
                errors.append({"field": ["files"], "message": self.user_errors[gid], "code": "INVALID"})
                continue
            self.nodes[gid]["alt"] = self._stored(gid, item["alt"])
            files.append({"id": gid, "alt": self.nodes[gid]["alt"]})
        if errors:
            return {"fileUpdate": {"files": None, "userErrors": errors}}
        if not files:
            return {"fileUpdate": None}
        return {"fileUpdate": {"files": files, "userErrors": []}}


class FakeCliProcess:
    """Stands in for `subprocess.run` of `shopify store execute`: reads the query and variable
    files the wrapper wrote, answers from a FakeStore, and writes the result to --output-file as
    UTF-8 the way the CLI does. Records each command and the raw variable-file bytes."""

    def __init__(self, store: FakeStore) -> None:
        self.store = store
        self.commands: list[list[str]] = []
        self.kwargs: list[dict] = []
        self.variable_bytes: list[bytes] = []

    def __call__(self, command, **kwargs):
        self.commands.append(list(command))
        self.kwargs.append(kwargs)
        args = {command[i]: command[i + 1] for i in range(len(command) - 1) if command[i].startswith("--")}
        query = Path(args["--query-file"]).read_text(encoding="utf-8")
        raw = Path(args["--variable-file"]).read_bytes()
        self.variable_bytes.append(raw)
        is_mutation = query.lstrip().startswith("mutation")
        if is_mutation and "--allow-mutations" not in command:
            raise AssertionError("a mutation was sent without --allow-mutations")
        data = self.store(query, json.loads(raw.decode("ascii")))
        Path(args["--output-file"]).write_bytes(json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8"))

        class Completed:
            returncode = 0
        return Completed()
