"""The check job end to end against fakes (spec 006 T061; plan "Check job" phases 0 to 11; FR-005, FR-006).

Seam: ``publisher.steps.run_check(job, deps)`` with every collaborator a fake (``flow_harness.Flow``): fake
Shopify behind the real ``AdminApiRunner``, fake ``npm``, ``FakeAws``, real git, SQLite and folders.
"""
import json

import pytest

from flow_fakes import write_tree
from flow_harness import CODE_COMMIT, HEAD_EXPORT, NEW_EXPORT, Flow

PHASES = ["history-retry", "workspace", "token", "theme", "export", "config", "build", "test", "typecheck",
          "check-dist", "snapshot", "preflight", "aws-identity", "marker-read", "dry-run", "summary", "verify"]


class Recording:
    """Wraps the job store so the test sees every phase the job sets, in order."""

    def __init__(self, flow):
        self.phases = []
        real_update = flow.store.update

        def update(job_id, **values):
            if "phase" in values:
                self.phases.append(values["phase"])
            return real_update(job_id, **values)

        flow.store.update = update


@pytest.fixture
def flow(tmp_path):
    return Flow(tmp_path)


def test_a_passing_check_runs_the_phases_in_order_and_ends_passed_with_a_snapshot(flow):
    recording = Recording(flow)

    job, outcome = flow.check()

    assert recording.phases == PHASES
    assert job.state == "passed" and outcome.state == "passed"
    assert job.snapshot_id and job.snapshot_id == outcome.snapshot_id
    assert flow.snapshots.get(job.snapshot_id).status == "kept"
    assert job.notice_state == "not-required", "a passed check posts nothing (A-11)"


def test_the_theme_is_read_and_the_export_run_in_process_through_the_admin_api_runner(flow):
    from shopify_admin import AdminApiRunner

    flow.check()

    workspace = flow.workspace_path()
    (theme,), (export,) = flow.theme_calls, flow.export_calls
    assert isinstance(theme["run"], AdminApiRunner) and theme["run"] is export["run"]
    assert theme["dest"] == workspace / "export" / "theme" and theme["theme_id"] == "123456789"
    assert export["export_dir"] == workspace / "export" and export["theme_dir"] == workspace / "export" / "theme"
    assert export["download"] is True
    assert len(flow.shopify.token_requests) == 1 and flow.shopify.graphql_requests, "a real grant and a real read"


def test_the_deploy_config_is_validated_before_anything_is_built(tmp_path):
    flow = Flow(tmp_path, deploy={"profile": "rosart-deploy", "bucket": "REPLACE_ME", "region": "ap-southeast-2",
                                  "distributionId": "E2ABCDEF123456"})

    job, outcome = flow.check()

    assert job.state == "failed" and not flow.children.npm_calls
    assert "bucket" in job.reason_text


def test_a_clean_build_then_the_four_checks_run_as_children_with_the_allowlisted_environment(flow):
    existing = flow.workspace.ensure(CODE_COMMIT, flow.children, flow.config.child_env())
    write_tree(existing, {"dist/stale/index.html": "stale page", ".astro/cache": "x", "node_modules/.vite/x": "y"})

    job, _ = flow.check()

    assert flow.children.npm_calls == [["npm", "run", "build"], ["npm", "test"], ["npm", "run", "typecheck"],
                                       ["npm", "run", "check:dist"]]
    assert set(map(str, flow.children.npm_cwds)) == {str(flow.workspace_path())}
    for env in flow.children.npm_envs:
        assert env["ASTRO_TELEMETRY_DISABLED"] == "1" and "AWS_SHARED_CREDENTIALS_FILE" not in env
        for secret in flow.config.secrets():
            assert secret.reveal() not in env.values()
    dist = flow.snapshots.folder(job.snapshot_id) / "dist"
    assert not (dist / "stale").exists(), "a planted stale page never reaches a snapshot"
    assert not (flow.workspace_path() / ".astro").exists()


def test_the_built_site_is_moved_into_the_snapshot_with_the_export_but_not_the_theme_code(flow):
    job, _ = flow.check()

    snapshot = flow.snapshots.folder(job.snapshot_id)
    assert (snapshot / "dist" / "index.html").is_file() and not (flow.workspace_path() / "dist").exists()
    assert (snapshot / "export" / "shopify" / "products.json").read_text() == NEW_EXPORT["export/shopify/products.json"]
    assert (snapshot / "export" / "theme-content.json").is_file()
    assert not (snapshot / "export" / "theme").exists(), "the raw theme code is never in a snapshot"
    assert not list(flow.snapshots.root.glob("*.partial"))


def test_the_dist_preflight_refuses_a_site_with_too_few_pages(tmp_path):
    from flow_harness import FakeChildren

    flow = Flow(tmp_path, children=FakeChildren(pages=3))

    job, _ = flow.check()

    assert job.state == "failed" and "pages" in job.reason_text
    assert flow.fake_aws is None, "AWS is never asked before the preflight passes"


def test_the_aws_identity_must_be_the_deploy_user(tmp_path):
    flow = Flow(tmp_path, aws={"identity_arn": "arn:aws:iam::123456789012:user/someone-else"})

    job, _ = flow.check()

    assert job.state == "failed" and job.reason_code == "aws-identity-mismatch"
    assert not flow.fake_aws.ran("s3"), "nothing is planned for the wrong identity"


def test_the_marker_the_check_saw_is_stored_canonical_and_null_when_absent(tmp_path):
    marker = {"site": "rosart", "publish_id": "pc-20261101T000000Z", "published_at": "2026-11-01T00:00:00Z",
              "content_hash": "a" * 64, "publisher": "pc"}
    present = Flow(tmp_path / "a", aws={"marker": marker})
    absent = Flow(tmp_path / "b")

    job_a, _ = present.check()
    job_b, _ = absent.check()

    assert present.snapshots.get(job_a.snapshot_id).marker_json == json.dumps(marker, sort_keys=True,
                                                                              separators=(",", ":"))
    assert absent.snapshots.get(job_b.snapshot_id).marker_json is None


def test_the_dry_run_covers_all_six_passes_uploads_nothing_and_excludes_the_marker(flow):
    job, _ = flow.check()

    syncs = flow.fake_aws.ran("s3", "sync")
    assert len(syncs) == 6 and all("--dryrun" in command for command in syncs)
    assert flow.fake_aws.real_syncs == 0 and not flow.fake_aws.marker_writes and not flow.fake_aws.ran("cloudfront")
    for command in syncs:
        if "--delete" in command:
            assert command[command.index("--exclude") + 1:].count("_publish.json") == 1
    plan = json.loads(flow.snapshots.get(job.snapshot_id).plan_json)
    assert "_publish.json" not in " ".join(plan["delete_keys"])
    assert plan["upload_count"] > 0 and "old-page/index.html" in plan["delete_keys"]


def test_the_summary_names_the_changed_product_the_counts_every_deleted_page_and_the_urls(flow):
    job, _ = flow.check()

    summary = json.loads(flow.snapshots.get(job.snapshot_id).summary_json)
    plan = json.loads(flow.snapshots.get(job.snapshot_id).plan_json)
    assert "New Toner" in summary["text"] and summary["changed"]["products"]["changed"] == ["New Toner"]
    assert summary["nothing_new"] is False
    assert plan["deleted_pages"] == ["/old-page/"]
    assert "/" in summary["spot_check_urls"] and "/products/toner/" in summary["spot_check_urls"]
    assert json.loads(job.summary_json) == summary


def test_nothing_changed_gives_nothing_new_and_a_snapshot_to_publish_anyway(tmp_path):
    flow = Flow(tmp_path, live_export=HEAD_EXPORT, aws={"no_deletes": True})

    job, outcome = flow.check()

    assert job.state == "passed"
    assert json.loads(job.summary_json)["nothing_new"] is True
    assert flow.snapshots.publishable(job.snapshot_id), "Publish is still offered (US1 scenario 3)"
    assert flow.snapshots.get(job.snapshot_id).status == "kept"


def test_a_passed_check_records_the_monotonic_time_and_boot_id_of_its_finish(flow):
    flow.clock.advance(100)

    job, _ = flow.check()

    snapshot = flow.snapshots.get(job.snapshot_id)
    assert snapshot.created_mono == flow.clock.mono() and snapshot.boot_id == "boot-1"
    assert flow.snapshots.publishable(snapshot.id)


FAILURES = {
    "shopify-refused": dict(setup=lambda f: setattr(f.shopify, "grant_error", "shop_not_permitted"),
                            phase="token"),
    "build-fails": dict(setup=lambda f: f.children.fail.update({("npm", "run", "build"): (1, "boom\nerror: bad\n")}),
                        phase="build"),
    "tests-fail": dict(setup=lambda f: f.children.fail.update({("npm", "test"): (1, "FAIL theme fidelity\n")}),
                       phase="test"),
    "aws-refused": dict(setup=lambda f: f.aws_kwargs.update(fail=lambda c: c[1:3] == ["sts", "get-caller-identity"]),
                        phase="aws-identity"),
}


@pytest.mark.parametrize("name", sorted(FAILURES))
def test_a_failure_at_any_phase_ends_failed_and_leaves_no_snapshot_folder(tmp_path, name):
    flow = Flow(tmp_path)
    FAILURES[name]["setup"](flow)
    recording = Recording(flow)

    job, outcome = flow.check()

    assert job.state == "failed" and outcome.state == "failed"
    assert recording.phases[-1] == FAILURES[name]["phase"]
    assert job.reason_code and job.reason_text and json.loads(job.failures_json)
    assert job.snapshot_id is None and job.notice_state == "pending"
    assert not (flow.snapshots.root.exists() and any(flow.snapshots.root.iterdir())), "no snapshot folder is left"
    assert flow.store.running() is None, "the lock is released"


def test_a_failed_check_keeps_the_raw_failure_line_in_the_job_log_and_no_secret(tmp_path):
    flow = Flow(tmp_path)
    secret = flow.config.shopify_client_secret.reveal()
    flow.children.fail[("npm", "test")] = (1, f"FAIL something {secret}\n")

    flow.check()

    log = flow.log_text()
    assert "FAIL something" in log and secret not in log


def test_a_pending_history_commit_is_retried_before_anything_else_and_the_summary_compares_against_it(tmp_path):
    flow = Flow(tmp_path)
    folder = flow.snapshots.folder("rosart-0-pending00000")
    write_tree(folder, {"dist/index.html": "x", **{k: v for k, v in NEW_EXPORT.items()}})
    earlier = flow.start("publish")
    flow.store.finish(earlier.id, "published", snapshot_id="rosart-0-pending00000", invalidation_id="I1",
                      export_commit_error="git failed")

    job, _ = flow.check()

    assert flow.store.get(earlier.id).export_commit and flow.store.get(earlier.id).export_commit_error is None
    summary = json.loads(job.summary_json)
    assert summary["changed"]["products"]["changed"] == [] and "New Toner" not in summary["text"], \
        "HEAD now holds the pending publish, so the summary no longer lists it"
