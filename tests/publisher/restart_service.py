"""A real publisher service process over the fake world, for the restart tests (spec 006 T093).

Usage: ``python restart_service.py <root>``. ``root`` is a folder holding a ``flow_harness.Flow`` world (the test
prepared a checked snapshot in it); the service reuses it across restarts. Real: the job runner, the child runner
with process groups, the watchdog, start-up recovery, the SIGTERM handler, the HTTP server on 127.0.0.1. Fake: the
Shopify Admin and theme read (in-process), Slack, ``npm`` and ``aws`` (the executables in ``bin/``, scripted by
``FAKE_AWS_SCENARIO``). Writes its port to ``<root>/port`` once it serves. ``PUBLISHER_FAULT_HOOK`` is honoured.
"""
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent.parent / "tools"), str(HERE.parent.parent)]

ROOT = Path(sys.argv[1])
BIN = HERE / "bin"
PATH = str(BIN) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")

from fake_slack import FakeSlack  # noqa: E402
from flow_harness import CODE_COMMIT, Flow  # noqa: E402
from publisher.__main__ import serve_app  # noqa: E402
from publisher.aws import ServiceAwsRunner, service_aws  # noqa: E402
from publisher.exportrepo import ExportRepo  # noqa: E402
from publisher.runner import ChildRunner, JobRunner, fault_hook_from_env  # noqa: E402
from publisher.scrub import JobLog  # noqa: E402
from publisher.slack import SlackNotifier  # noqa: E402
from publisher.web import App  # noqa: E402

flow = Flow(ROOT, extra_env={"PATH": PATH, "PUBLISHER_BIND": "127.0.0.1:0"})
fault = fault_hook_from_env(os.environ)
scenario = os.environ.get("FAKE_AWS_SCENARIO", "")


def deps_for(job):
    deps = flow.deps()
    children = ChildRunner(flow.store, job.id, flow.clock, flow.scrubber, fault=fault)
    deps.children = children
    deps.log = JobLog(flow.config.data / job.log_path, flow.scrubber)
    deps.export_repo = ExportRepo(flow.bare, children, flow.config.child_env(), flow.config.tmp_dir / "git")

    def make_aws(deploy, temp_dir, timeout_s=None):
        runner = ServiceAwsRunner(flow.config.aws_dir, children, executable=str(BIN / "aws"),
                                  timeout=int(timeout_s or 3600),
                                  env=lambda: {**flow.config.child_env(aws=True), "FAKE_AWS_SCENARIO": scenario})
        return service_aws(deploy, runner, temp_dir)

    deps.make_aws = make_aws
    return deps


slack = FakeSlack()
notifier = SlackNotifier(flow.config, flow.store, slack.opener, flow.clock.sleep, flow.scrubber)
runner = JobRunner(flow.store, flow.snapshots, deps_for, notifier, flow.config, CODE_COMMIT, flow.scrubber)
app = App(flow.config, flow.store, flow.clock, code_commit=CODE_COMMIT, scrubber=flow.scrubber,
          snapshots=flow.snapshots, runner=runner, export_repo=flow.export_repo)
app.workspace = flow.workspace

sys.exit(serve_app(app, announce=lambda port: (ROOT / "port").write_text(str(port), encoding="utf-8")))
