"""The service's AWS runner: ``publish.Aws`` with its guard, over a credentials file in a tmpfs.

This is the only place in the service that starts the ``aws`` CLI. Every command still goes through
``publish.Aws.run`` and so through ``assert_command_allowed`` before this runner sees it. The child
reads its one credential source from ``<aws_dir>/credentials`` and ``<aws_dir>/config`` (written at
start-up, mode 0600, on tmpfs) and inherits no key variable (research R-8).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, Optional

from publish import STRIPPED_ENV, Aws, AwsResult, DeployConfig, PublishError, Runner


class ServiceAwsRunner:
    """A ``publish.Runner`` that runs a guarded ``["aws", ...]`` command with the service's credentials."""

    def __init__(self, aws_dir: Path, executable: Optional[str] = None, timeout: int = 3600,
                 run: Callable[..., subprocess.CompletedProcess] = subprocess.run) -> None:
        self.executable = executable or shutil.which("aws")
        if not self.executable:
            raise PublishError("the aws CLI is not on PATH: the image must install AWS CLI v2")
        self.aws_dir = Path(aws_dir)
        self.timeout = timeout
        self._run = run

    def environment(self) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k.upper() not in STRIPPED_ENV}
        env.update({
            "AWS_SHARED_CREDENTIALS_FILE": str(self.aws_dir / "credentials"),
            "AWS_CONFIG_FILE": str(self.aws_dir / "config"),
            "AWS_EC2_METADATA_DISABLED": "true",
            "AWS_PAGER": "",
        })
        return env

    def __call__(self, command: list[str]) -> AwsResult:
        try:
            completed = self._run([self.executable, *command[1:]], capture_output=True, text=True,
                                  encoding="utf-8", errors="replace", env=self.environment(),
                                  timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            raise PublishError(f"aws {' '.join(command[1:3])} timed out after {self.timeout}s") from exc
        return AwsResult(completed.returncode, completed.stdout or "", completed.stderr or "")


def service_aws(config: DeployConfig, runner: Runner, temp_dir: Optional[Path] = None) -> Aws:
    """The guarded ``publish.Aws`` the service uses; ``temp_dir`` is the job's folder for marker files."""
    return Aws(config, runner, temp_dir=temp_dir)
