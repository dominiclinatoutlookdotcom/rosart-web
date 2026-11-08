"""The real publisher server over the whole fake world (spec 006 T067): ``FlowWeb`` = ``Flow`` + ``JobRunner`` + ``App``.

Jobs started through ``POST /check`` and ``POST /publish`` run on the runner's worker thread against the fakes of
``flow_harness.Flow``; Slack goes to ``FakeSlack``. ``web`` is a ``web_harness.Harness`` (client for the server).
"""
from __future__ import annotations

import threading
from pathlib import Path

from fake_slack import FakeSlack
from flow_harness import CODE_COMMIT, Flow
from web_harness import Harness


class FlowWeb:
    def __init__(self, tmp_path: Path, base_path: str = "/rosart", **flow_kwargs) -> None:
        from publisher.runner import JobRunner
        from publisher.slack import SlackNotifier
        from publisher.web import App, make_server

        extra_env = {"PUBLISHER_BASE_PATH": base_path, **(flow_kwargs.pop("extra_env", None) or {})}
        self.flow = flow = Flow(tmp_path, extra_env=extra_env, **flow_kwargs)
        self.slack = FakeSlack()
        notifier = SlackNotifier(flow.config, flow.store, self.slack.opener, flow.clock.sleep, flow.scrubber)
        self.runner = JobRunner(flow.store, flow.snapshots, self._deps_for, notifier, flow.config, CODE_COMMIT,
                                flow.scrubber)
        app = App(flow.config, flow.store, flow.clock, code_commit=CODE_COMMIT, scrubber=flow.scrubber,
                  snapshots=flow.snapshots, runner=self.runner, export_repo=flow.export_repo)
        server = make_server(app, flow.config.bind)
        self.web = Harness(base_path, flow.config, flow.store, app, server, flow.clock, server.server_address[1])
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def _deps_for(self, job):
        from publisher.scrub import JobLog

        log = JobLog(self.flow.config.data / job.log_path, self.flow.scrubber)
        deps = self.flow.deps()
        deps.log = log
        return deps

    def wait(self, seconds: float = 60) -> None:
        assert self.runner.wait_idle(seconds), "the job did not finish in time"

    def close(self) -> None:
        self.runner.wait_idle(30)
        self.web.close()
