"""The SEO apply step: approval gate, scope and drift checks, batched writes with a rollback log,
read-back, rollback and the dry run. Every test runs against an in-memory fake store (no network,
no Shopify CLI)."""
import hashlib
import json

import pytest

from fake_shopify import FakeStore
from seo_apply import main
from seo_plan import MECHANISMS

STORE = "rosart-mhfmyuxq.myshopify.com"
P1, P2, P3 = "gid://shopify/Product/1", "gid://shopify/Product/2", "gid://shopify/Product/3"
C1, CI1 = "gid://shopify/Collection/1", "gid://shopify/CollectionImage/1"
PG1 = "gid://shopify/Page/1"
M1, M2, M3 = "gid://shopify/MediaImage/1", "gid://shopify/MediaImage/2", "gid://shopify/MediaImage/3"
CAT_OLD, CAT_NEW = "gid://shopify/TaxonomyCategory/hb-3-2-9-20", "gid://shopify/TaxonomyCategory/hb-3-2-9-17-2"


def desc(handle: str) -> str:
    """A 130-character SEO description unique to the handle (passes the checker's range)."""
    return (f"Rosart {handle} rose skincare, " + "gentle daily care with rose water, " * 6)[:130]


def change(resource, handle, field, gid, before, after, **extra):
    mechanism, scope = MECHANISMS[(resource, field)]
    entry = {"id": f"{resource}/{handle}/{field}" + (f"/{extra['file']}" if "file" in extra else ""),
             "resource": resource, "gid": gid, "handle": handle, "field": field,
             "before": before, "after": after,
             "sources": [{"ref": "test", "text": f"{after} {before or ''}"}],
             "mechanism": mechanism, "scope": scope}
    entry.update(extra)
    return entry


def make_plan(changes, kept=None):
    return {"store": STORE, "skus": ["RARFT330"], "scopes": {}, "changes": changes, "kept": kept or [],
            "decorative": [], "findings": [], "references": []}


def write_plan(tmp_path, plan):
    path = tmp_path / "plan.json"
    data = (json.dumps(plan, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    path.write_bytes(data)
    return path, hashlib.sha256(data).hexdigest()


def basic_plan():
    return make_plan([
        change("product", "toner", "seo.title", P1, "Old toner title", "Rose Facial Toner 330ml | Rosart"),
    ])


def basic_store():
    store = FakeStore()
    store.add_product(P1, "toner", seo_title="Old toner title", category=CAT_OLD)
    return store


def run(tmp_path, store, *args, sleep=None):
    sleeps = [] if sleep is None else sleep
    return main(["--log-dir", str(tmp_path), "--pause", "0.5", *args],
                runner_factory=lambda allow_mutations: store, sleep=sleeps.append)


# ---------------------------------------------------------------- the approval gate

def test_write_refuses_a_plan_whose_hash_was_not_approved(tmp_path, capsys):
    path, _ = write_plan(tmp_path, basic_plan())
    store = basic_store()
    code = run(tmp_path, store, "--plan", str(path), "--approved", "0" * 64, "--write")
    assert code == 1
    assert "does not match" in capsys.readouterr().out
    assert store.calls == []


def test_write_refuses_after_the_approved_file_changed_by_one_byte(tmp_path, capsys):
    path, approved = write_plan(tmp_path, basic_plan())
    path.write_bytes(path.read_bytes() + b" ")
    store = basic_store()
    assert run(tmp_path, store, "--plan", str(path), "--approved", approved, "--write") == 1
    assert store.calls == []


def test_write_needs_an_approval_hash(tmp_path, capsys):
    path, _ = write_plan(tmp_path, basic_plan())
    store = basic_store()
    assert run(tmp_path, store, "--plan", str(path), "--write") == 1
    assert "--approved" in capsys.readouterr().out
    assert store.calls == []


def test_refuses_a_plan_that_fails_the_seo_check(tmp_path, capsys):
    plan = make_plan([change("product", "toner", "seo.title", P1, "Old toner title", "x" * 61)])
    path, approved = write_plan(tmp_path, plan)
    store = basic_store()
    assert run(tmp_path, store, "--plan", str(path), "--approved", approved, "--write") == 1
    out = capsys.readouterr().out
    assert "seo_check" in out and "61 characters" in out
    assert store.calls == []


def test_refuses_a_plan_for_another_store(tmp_path, capsys):
    plan = basic_plan()
    plan["store"] = "someone-else.myshopify.com"
    path, approved = write_plan(tmp_path, plan)
    store = basic_store()
    assert run(tmp_path, store, "--plan", str(path), "--approved", approved, "--write") == 1
    assert "someone-else" in capsys.readouterr().out
    assert store.calls == []


def test_the_approved_hash_lets_the_write_through(tmp_path):
    path, approved = write_plan(tmp_path, basic_plan())
    store = basic_store()
    assert run(tmp_path, store, "--plan", str(path), "--approved", approved, "--write") == 0
    assert store.value(P1, "seo.title") == "Rose Facial Toner 330ml | Rosart"


# ---------------------------------------------------------------- scopes, dry run, manual entries

def mixed_plan():
    """One change per mechanism, plus a manual homepage entry."""
    return make_plan([
        change("shop", "home", "seo.title", "gid://shopify/Shop/1", None, "Organic Rose Skincare | Rosart"),
        change("page", "contact", "seo.title", PG1, "", "Contact Us | Rosart"),
        change("collection", "face-care", "descriptionHtml", C1, "",
               "<p>Rosart Face Care holds rose creams. Every product is made with rose.</p>"),
        change("collection", "face-care", "image.altText", CI1, "", "Rosart rose cream with its box"),
        change("product", "toner", "seo.title", P1, "Old toner title", "Rose Facial Toner 330ml | Rosart"),
        change("product", "toner", "category", P1, CAT_OLD, CAT_NEW),
        change("product_media", "toner", "alt", M1, "", "Rose Facial Toner 330ml, front", file="01-a.png"),
        change("file", "logo.png", "alt", M2, "", "Rosart logo", file="logo.png"),
    ])


def mixed_store(scopes=None):
    store = FakeStore(scopes=scopes)
    store.add_page(PG1, "contact")
    store.add_collection(C1, "face-care", image_id=CI1)
    store.add_product(P1, "toner", seo_title="Old toner title", category=CAT_OLD)
    store.add_file(M1)
    store.add_file(M2)
    return store


def test_write_refuses_when_a_needed_scope_is_missing_and_names_it(tmp_path, capsys):
    path, approved = write_plan(tmp_path, mixed_plan())
    store = mixed_store(scopes=["read_products", "read_files", "read_content", "write_content"])
    assert run(tmp_path, store, "--plan", str(path), "--approved", approved, "--write") == 1
    out = capsys.readouterr().out
    assert "write_files, write_products" in out
    assert store.mutations() == []
    assert len(store.calls) == 1 and "accessScopes" in store.calls[0][0]


def test_dry_run_is_the_default_and_never_mutates(tmp_path, capsys):
    path, _ = write_plan(tmp_path, mixed_plan())
    store = mixed_store()
    allowed = []
    code = main(["--plan", str(path), "--log-dir", str(tmp_path)],
                runner_factory=lambda allow: allowed.append(allow) or store, sleep=lambda s: None)
    assert code == 0
    assert allowed == [False]
    assert store.mutations() == []
    assert list(tmp_path.glob("apply-log-*")) == []
    out = capsys.readouterr().out
    assert "DRY RUN" in out
    assert "product seo.title: 1" in out and "product_media alt: 1" in out and "file alt: 1" in out
    assert "7 change(s) ready" in out


def test_dry_run_prints_the_plan_hash_to_approve(tmp_path, capsys):
    path, approved = write_plan(tmp_path, mixed_plan())
    run(tmp_path, mixed_store(), "--plan", str(path))
    assert approved in capsys.readouterr().out


def test_dry_run_reports_missing_scopes_and_exits_non_zero(tmp_path, capsys):
    path, _ = write_plan(tmp_path, mixed_plan())
    store = mixed_store(scopes=["read_products", "write_content"])
    assert run(tmp_path, store, "--plan", str(path)) == 1
    assert "write_files, write_products" in capsys.readouterr().out
    assert store.mutations() == []


def test_dry_run_reports_drift(tmp_path, capsys):
    path, _ = write_plan(tmp_path, mixed_plan())
    store = mixed_store()
    store.nodes[P1]["seo"]["title"] = "Edited in the admin since the plan"
    assert run(tmp_path, store, "--plan", str(path)) == 1
    out = capsys.readouterr().out
    assert "DRIFT product/toner/seo.title" in out and "Edited in the admin since the plan" in out


def test_manual_entries_are_never_written_and_are_listed_for_the_owner(tmp_path, capsys):
    path, approved = write_plan(tmp_path, mixed_plan())
    store = mixed_store()
    assert run(tmp_path, store, "--plan", str(path), "--approved", approved, "--write") == 0
    out = capsys.readouterr().out
    assert "MANUAL shop/home/seo.title" in out and "Organic Rose Skincare | Rosart" in out
    assert not any("Shop" in json.dumps(v) for _, v in store.mutations())


# ---------------------------------------------------------------- writing: drift, refusals, batches

def product_plan(n, start=1):
    return make_plan([change("product", f"p{i}", "seo.title", f"gid://shopify/Product/{i}", f"Old title {i}",
                             f"Rose product number {i} | Rosart") for i in range(start, start + n)])


def product_store(n, start=1):
    store = FakeStore()
    for i in range(start, start + n):
        store.add_product(f"gid://shopify/Product/{i}", f"p{i}", seo_title=f"Old title {i}")
    return store


def apply(tmp_path, plan, store, *extra, sleep=None):
    path, approved = write_plan(tmp_path, plan)
    return run(tmp_path, store, "--plan", str(path), "--approved", approved, "--write", *extra, sleep=sleep)


def read_log(tmp_path):
    logs = sorted(tmp_path.glob("apply-log-*.jsonl"))
    assert len(logs) == 1
    return logs[0], [json.loads(line) for line in logs[0].read_text(encoding="utf-8").splitlines()]


def test_drifted_value_is_skipped_never_overwritten_and_the_rest_is_written(tmp_path, capsys):
    store = product_store(2)
    store.nodes["gid://shopify/Product/1"]["seo"]["title"] = "Owner edit"
    assert apply(tmp_path, product_plan(2), store) == 1
    assert store.value("gid://shopify/Product/1", "seo.title") == "Owner edit"
    assert store.value("gid://shopify/Product/2", "seo.title") == "Rose product number 2 | Rosart"
    out = capsys.readouterr().out
    assert "DRIFT product/p1/seo.title" in out and "Owner edit" in out
    assert not any(v["product"]["id"] == "gid://shopify/Product/1" for _, v in store.mutations())


def test_a_value_already_at_after_is_not_written_again(tmp_path):
    store = product_store(1)
    store.nodes["gid://shopify/Product/1"]["seo"]["title"] = "Rose product number 1 | Rosart"
    assert apply(tmp_path, product_plan(1), store) == 0
    assert store.mutations() == []


def test_a_null_payload_without_user_errors_is_a_failure_and_stops_the_run(tmp_path, capsys):
    store = product_store(12)
    store.silent_null.add("gid://shopify/Product/2")
    assert apply(tmp_path, product_plan(12), store) == 1
    out = capsys.readouterr().out
    assert "FAILED product/p2/seo.title" in out and "silent refusal" in out
    assert "STOPPED after batch 1" in out
    # The second batch (products 11 and 12) is never reached.
    assert store.value("gid://shopify/Product/11", "seo.title") == "Old title 11"


def test_user_errors_are_a_failure(tmp_path, capsys):
    store = product_store(1)
    store.user_errors["gid://shopify/Product/1"] = "Access denied"
    assert apply(tmp_path, product_plan(1), store) == 1
    assert "Access denied" in capsys.readouterr().out


def test_a_file_missing_from_a_silent_file_update_is_a_failure(tmp_path, capsys):
    plan = make_plan([change("file", "a.png", "alt", M1, "", "Rosart logo", file="a.png"),
                      change("file", "b.png", "alt", M2, "", "Rosart rose field", file="b.png")])
    store = FakeStore()
    store.add_file(M1)
    store.add_file(M2)
    store.silent_null.add(M2)
    assert apply(tmp_path, plan, store) == 1
    out = capsys.readouterr().out
    assert "FAILED file/b.png/alt/b.png" in out and "FAILED file/a.png" not in out


def test_writes_go_in_batches_of_ten_with_a_pause_between_batches(tmp_path):
    store = product_store(23)
    sleeps = []
    assert apply(tmp_path, product_plan(23), store, sleep=sleeps) == 0
    assert len(store.mutations()) == 23
    assert sleeps == [0.5, 0.5]


def test_files_in_a_batch_share_one_file_update(tmp_path):
    plan = make_plan([change("file", f"f{i}.png", "alt", f"gid://shopify/MediaImage/{i}", "",
                             f"Rosart image {i}", file=f"f{i}.png") for i in range(1, 13)])
    store = FakeStore()
    for i in range(1, 13):
        store.add_file(f"gid://shopify/MediaImage/{i}")
    assert apply(tmp_path, plan, store) == 0
    sizes = [len(v["files"]) for q, v in store.mutations()]
    assert sizes == [10, 2]


def test_writing_one_seo_field_keeps_the_other(tmp_path):
    store = product_store(1)
    store.nodes["gid://shopify/Product/1"]["seo"]["description"] = "Kept description"
    assert apply(tmp_path, product_plan(1), store) == 0
    assert store.value("gid://shopify/Product/1", "seo.description") == "Kept description"


def test_every_planned_mechanism_writes_its_field(tmp_path):
    store = mixed_store()
    assert apply(tmp_path, mixed_plan(), store) == 0
    assert store.value(PG1, "title_tag") == "Contact Us | Rosart"
    assert store.value(C1, "descriptionHtml").startswith("<p>Rosart Face Care")
    assert store.value(C1, "image.altText") == "Rosart rose cream with its box"
    assert store.value(P1, "seo.title") == "Rose Facial Toner 330ml | Rosart"
    assert store.value(P1, "category") == CAT_NEW
    assert store.value(M1, "alt") == "Rose Facial Toner 330ml, front"
    assert store.value(M2, "alt") == "Rosart logo"


# ---------------------------------------------------------------- the rollback log and read-back

def test_the_live_before_value_is_logged_before_the_write(tmp_path):
    store = product_store(1)
    seen_at_write = []

    def check_log(op, variables):
        _, lines = read_log(tmp_path)
        seen_at_write.append(lines)

    store.on_mutation = check_log
    assert apply(tmp_path, product_plan(1), store) == 0
    first = seen_at_write[0]
    assert first[0]["type"] == "before"
    assert first[0]["id"] == "product/p1/seo.title"
    assert first[0]["before"] == "Old title 1"
    assert first[0]["after"] == "Rose product number 1 | Rosart"
    assert first[0]["target"] == "gid://shopify/Product/1"


def test_the_log_records_the_live_value_not_the_plan_value(tmp_path):
    # The plan says "" (unset); live is null: the log keeps the exact live value for a true restore.
    plan = make_plan([change("page", "contact", "seo.title", PG1, "", "Contact Us | Rosart")])
    store = FakeStore()
    store.add_page(PG1, "contact", title_tag=None)
    assert apply(tmp_path, plan, store) == 0
    _, lines = read_log(tmp_path)
    assert [line["before"] for line in lines if line["type"] == "before"] == [None]


def test_read_back_mismatch_is_reported_and_stops_after_that_batch(tmp_path, capsys):
    store = product_store(12)
    store.mangle["gid://shopify/Product/3"] = "Rose product number 3 | Rosart (trimmed)"
    assert apply(tmp_path, product_plan(12), store) == 1
    out = capsys.readouterr().out
    assert "MISMATCH product/p3/seo.title" in out and "(trimmed)" in out
    assert "STOPPED after batch 1" in out
    assert store.value("gid://shopify/Product/11", "seo.title") == "Old title 11"


def test_non_ascii_values_round_trip_byte_exact(tmp_path):
    after = "Rose Face Soufflé Cream, Bulgarian rose | Rosart"
    plan = make_plan([change("product", "souffle", "seo.title", P1, "Old title", after)])
    store = FakeStore()
    store.add_product(P1, "souffle", seo_title="Old title")
    assert apply(tmp_path, plan, store) == 0
    assert store.value(P1, "seo.title").encode("utf-8") == after.encode("utf-8")
    log, lines = read_log(tmp_path)
    assert "Soufflé".encode("utf-8") in log.read_bytes()
    assert lines[0]["after"] == after


# ---------------------------------------------------------------- rollback

def applied(tmp_path, plan, store):
    assert apply(tmp_path, plan, store) == 0
    log, _ = read_log(tmp_path)
    moved = tmp_path / "logs" / log.name
    moved.parent.mkdir()
    log.rename(moved)
    return moved


def rollback(tmp_path, store, log, *extra):
    return run(tmp_path, store, "--rollback", str(log), *extra)


def test_rollback_restores_every_logged_value(tmp_path):
    store = mixed_store()
    store.add_page(PG1, "contact", title_tag=None)
    log = applied(tmp_path, mixed_plan(), store)
    assert rollback(tmp_path, store, log, "--write") == 0
    assert store.value(PG1, "title_tag") is None
    assert store.value(C1, "descriptionHtml") == ""
    assert store.value(C1, "image.altText") in ("", None)
    assert store.value(P1, "seo.title") == "Old toner title"
    assert store.value(P1, "category") == CAT_OLD
    assert store.value(M1, "alt") == "" and store.value(M2, "alt") == ""


def test_rollback_skips_a_value_edited_after_the_apply(tmp_path, capsys):
    store = product_store(2)
    log = applied(tmp_path, product_plan(2), store)
    store.nodes["gid://shopify/Product/1"]["seo"]["title"] = "Edited after the apply"
    assert rollback(tmp_path, store, log, "--write") == 1
    assert store.value("gid://shopify/Product/1", "seo.title") == "Edited after the apply"
    assert store.value("gid://shopify/Product/2", "seo.title") == "Old title 2"
    assert "DRIFT product/p1/seo.title" in capsys.readouterr().out


def test_rollback_without_write_is_a_dry_run(tmp_path, capsys):
    store = product_store(1)
    log = applied(tmp_path, product_plan(1), store)
    before = len(store.mutations())
    assert rollback(tmp_path, store, log) == 0
    assert len(store.mutations()) == before
    assert "DRY RUN" in capsys.readouterr().out


def test_rollback_writes_its_own_log_so_it_can_be_undone(tmp_path):
    store = product_store(1)
    log = applied(tmp_path, product_plan(1), store)
    assert rollback(tmp_path, store, log, "--write") == 0
    _, lines = read_log(tmp_path)
    assert lines[0]["before"] == "Rose product number 1 | Rosart"
    assert lines[0]["after"] == "Old title 1"


def test_rollback_refuses_when_a_scope_is_missing(tmp_path, capsys):
    store = product_store(1)
    log = applied(tmp_path, product_plan(1), store)
    store.scopes = ["read_products"]
    before = len(store.mutations())
    assert rollback(tmp_path, store, log, "--write") == 1
    assert len(store.mutations()) == before
    assert "write_products" in capsys.readouterr().out


# ---------------------------------------------------------------- end to end through the CLI file transport

def test_non_ascii_survives_the_cli_file_transport_end_to_end(tmp_path):
    from fake_shopify import FakeCliProcess
    from shopify_cli import ShopifyCliFileRunner

    after = "Rose Face Soufflé Cream, Bulgarian rose | Rosart"
    plan = make_plan([change("product", "souffle", "seo.title", P1, "Old title", after)])
    store = FakeStore()
    store.add_product(P1, "souffle", seo_title="Old title")
    process = FakeCliProcess(store)
    path, approved = write_plan(tmp_path, plan)
    code = main(["--plan", str(path), "--approved", approved, "--write", "--log-dir", str(tmp_path)],
                runner_factory=lambda allow: ShopifyCliFileRunner(STORE, executable="shopify",
                                                                  allow_mutations=allow, run_process=process),
                sleep=lambda s: None)
    assert code == 0
    assert store.value(P1, "seo.title").encode("utf-8") == after.encode("utf-8")
    assert all(all(b < 128 for b in raw) for raw in process.variable_bytes)


def test_a_cli_error_mid_run_stops_and_points_at_the_rollback_log(tmp_path, capsys):
    from shopify_cli import ShopifyCliError

    store = product_store(1)

    def fail(op, variables):
        raise ShopifyCliError("Throttled")

    store.on_mutation = fail
    assert apply(tmp_path, product_plan(1), store) == 1
    out = capsys.readouterr().out
    log, lines = read_log(tmp_path)
    assert "Throttled" in out and str(log) in out
    assert lines[0]["before"] == "Old title 1"
