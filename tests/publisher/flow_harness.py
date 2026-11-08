"""A whole fake world for the check and publish flows (spec 006 Phase 4B): every collaborator is a fake.

``Flow`` builds a workspace over a real bare export repository, a fake ``npm`` (``FakeChildren``), the fake
Shopify Admin behind the real ``AdminApiRunner``, ``FakeAws`` behind ``publish.Aws``, a fake clock, real SQLite
and real folders, and wires them into ``publisher.steps.Deps``. Nothing opens a socket.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Optional

import publish
from fake_aws import FakeAws
from fake_clock import FakeClock
from fake_shopify_admin import FakeShopifyAdmin
from flow_fakes import PlainChildren, git, make_bare_repo, write_tree
from publisher.runner import Completed
from web_harness import ENV

DEPLOY = {"profile": "rosart-deploy", "bucket": "rosart-test-bucket", "region": "ap-southeast-2",
          "distributionId": "E2ABCDEF123456"}
OLD_PRODUCTS = json.dumps([{"id": "1", "handle": "toner", "title": "Old Toner"}])
NEW_PRODUCTS = json.dumps([{"id": "1", "handle": "toner", "title": "New Toner"}])
HEAD_EXPORT = {"export/shopify/products.json": OLD_PRODUCTS, "export/theme-content.json": "{}",
               "export/images/a.jpg": b"\xff\xd8old"}
NEW_EXPORT = {"export/shopify/products.json": NEW_PRODUCTS, "export/theme-content.json": "{}",
              "export/images/a.jpg": b"\xff\xd8old"}
CODE_COMMIT = "deadbeef1234"


class FakeChildren(PlainChildren):
    """Real git, fake ``npm``: ``npm run build`` writes a believable ``dist/`` into the working folder."""

    def __init__(self, pages: int = 12, fail: Optional[dict[tuple, tuple[int, str]]] = None,
                 hook: Optional[Callable[[list[str], Optional[Path]], None]] = None, mono=lambda: 0.0) -> None:
        super().__init__(mono)
        self.pages = pages
        self.fail = dict(fail or {})
        self.hook = hook
        self.npm_calls: list[list[str]] = []
        self.npm_envs: list[dict[str, str]] = []
        self.npm_cwds: list[Optional[Path]] = []

    def run(self, argv, env, cwd, timeout_mono):
        if argv[0] != "npm":
            return super().run(argv, env, cwd, timeout_mono)
        self.npm_calls.append(list(argv))
        self.npm_envs.append(dict(env))
        self.npm_cwds.append(cwd)
        if self.hook:
            self.hook(list(argv), cwd)
        if tuple(argv) in self.fail:
            code, output = self.fail[tuple(argv)]
            return Completed(code, output, "")
        if tuple(argv) == ("npm", "run", "build"):
            self.write_dist(Path(cwd) / "dist")
        return Completed(0, f"ok: {' '.join(argv)}\n", "")

    def write_dist(self, dist: Path) -> None:
        files = {f"p{n}/index.html": f"<html>page {n}</html>" for n in range(self.pages)}
        files.update({"index.html": "<html>home</html>", "404.html": "<html>404</html>", "robots.txt": "User-agent: *",
                      "sitemap-index.xml": "<sitemapindex/>", "_astro/app.Ab12.js": "console.log(1)"})
        write_tree(dist, files)


class Flow:
    def __init__(self, tmp_path: Path, *, head_export=None, live_export=None, aws: Optional[dict] = None,
                 children: Optional[FakeChildren] = None, deploy: Optional[dict] = None,
                 extra_env: Optional[dict] = None) -> None:
        from publisher.config import load_config
        from publisher.exportrepo import ExportRepo
        from publisher.jobs import JobStore
        from publisher.scrub import JobLog, Scrubber
        from publisher.snapshots import SnapshotStore
        from publisher.workspace import Workspace

        self.tmp = tmp_path
        self.clock = FakeClock()
        self.live_export = dict(NEW_EXPORT if live_export is None else live_export)
        self.config = load_config(dict(ENV, PUBLISHER_DATA=str(tmp_path / "data"), PUBLISHER_BIND="127.0.0.1:0",
                                       PUBLISHER_EXPORT_REPO=str(tmp_path / "export.git"), **(extra_env or {})),
                                  tmp_dir=tmp_path / "tmp")
        self.scrubber = Scrubber()
        for secret in self.config.secrets():
            self.scrubber.register(secret)
        self.image = tmp_path / "image"
        write_tree(self.image, {"package.json": "{}", "src/site.ts": "x", "node_modules/.bin/astro": "bin",
                                "export/from-image.json": "old",
                                "deploy.config.json": json.dumps(deploy or DEPLOY)})
        # A world rebuilt over the same folder (the restart tests' service process) keeps its export repository.
        self.bare = (tmp_path / "export.git" if (tmp_path / "export.git").exists()
                     else make_bare_repo(tmp_path / "export.git", HEAD_EXPORT if head_export is None else head_export))
        self.children = children or FakeChildren(mono=self.clock.mono)
        self.store = JobStore(self.config.data / "publisher.sqlite3", site="rosart", clock=self.clock)
        self.snapshots = SnapshotStore(self.store, self.config.data, "rosart", self.clock, self.config.snapshot_window_s)
        self.workspace = Workspace(self.config.data / "work", "rosart", self.image, self.bare)
        self.export_repo = ExportRepo(self.bare, self.children, self.config.child_env(), self.config.tmp_dir / "git")
        self.shopify = FakeShopifyAdmin(self.clock, backend=lambda query, variables: {"shop": {"name": "Rosart"}})
        self.shopify.client_id = "client-id-planted"
        self.shopify.client_secret = "client-secret-planted"
        self.aws_kwargs = dict(aws or {})
        self.fake_aws: Optional[FakeAws] = None
        self.theme_calls: list[dict] = []
        self.export_calls: list[dict] = []
        self.runners: list[Any] = []
        self.log_path = self.config.data / "logs" / "rosart" / "job.log"
        self.log = JobLog(self.log_path, self.scrubber)

    # --- the fakes the steps call -----------------------------------------------------------

    def make_shopify(self, deadline_mono: float):
        from shopify_admin import AdminApiRunner, token_source_from_config

        tokens = token_source_from_config(self.config.token_config(), shop=self.shopify.shop,
                                          opener=self.shopify.opener, clock=self.clock.mono)
        runner = AdminApiRunner(self.shopify.shop, tokens, self.shopify.opener, self.clock.sleep, deadline_mono,
                                clock=self.clock.mono)
        self.runners.append(runner)
        return tokens, runner

    def read_theme(self, run, fetch_url, theme_id, dest, require_main=True):
        self.theme_calls.append({"run": run, "theme_id": theme_id, "dest": Path(dest)})
        write_tree(Path(dest), {"layout/theme.liquid": "licensed theme code"})

    def run_export(self, run, fetch, sleep, export_dir, theme_dir, download=True, log=lambda message: None):
        self.export_calls.append({"run": run, "export_dir": Path(export_dir), "theme_dir": Path(theme_dir),
                                  "download": download})
        log("shop")
        run("query { shop { name } }", {})              # a real read through the token source and fake Shopify
        write_tree(Path(export_dir).parent, self.live_export)
        return {"products": 1}

    def make_aws(self, config, temp_dir, timeout_s=None):
        if self.fake_aws is None:
            self.fake_aws = FakeAws(config, **self.aws_kwargs)
        return publish.Aws(config, self.fake_aws, temp_dir=temp_dir)

    # --- wiring -----------------------------------------------------------------------------

    def deps(self):
        from publisher.steps import Deps

        return Deps(config=self.config, store=self.store, snapshots=self.snapshots, clock=self.clock,
                    children=self.children, log=self.log, scrubber=self.scrubber, workspace=self.workspace,
                    export_repo=self.export_repo, code_commit=CODE_COMMIT, make_shopify=self.make_shopify,
                    fetch=lambda url: b"", make_aws=self.make_aws, theme_id="123456789",
                    read_theme=self.read_theme, run_export=self.run_export, sleep=self.clock.sleep)

    def start(self, kind: str, person: str = "denise", snapshot_id: Optional[str] = None):
        return self.store.start_job(kind, person, "Denise Hill", "denise@example.org", CODE_COMMIT, 1200)

    def check(self, **overrides):
        """Start a check job, run it, return ``(job row after the run, outcome)``."""
        from publisher.steps import run_check

        job = self.start("check")
        deps = self.deps()
        for name, value in overrides.items():
            setattr(deps, name, value)
        outcome = run_check(job, deps)
        return self.store.get(job.id), outcome

    def checked(self) -> str:
        """A passed check; returns its snapshot id."""
        job, _ = self.check()
        assert job.state == "passed", job.reason_text
        return job.snapshot_id

    def publish(self, snapshot_id: str, person: str = "denise", **overrides):
        """Start a publish of ``snapshot_id`` (through the start guard), run it, return ``(job row, outcome)``."""
        from publisher.steps import run_publish, start_publish

        deps = self.deps()
        for name, value in overrides.items():
            setattr(deps, name, value)
        job = start_publish(deps, snapshot_id, person, "Denise Hill", "denise@example.org")
        outcome = run_publish(job, deps)
        return self.store.get(job.id), outcome

    def workspace_path(self) -> Path:
        return self.workspace.current()

    def log_text(self) -> str:
        return self.log_path.read_text(encoding="utf-8")


def head_message(bare: Path, rev: str = "HEAD") -> str:
    return git("--git-dir", str(bare), "log", "-1", "--format=%B", rev)
