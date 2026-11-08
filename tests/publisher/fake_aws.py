"""A fake AWS CLI for the publisher tests (spec 006 T026; FR-023: no test touches AWS).

``FakeAws`` is a ``publish.Runner``: inject it into ``publish.Aws``. It answers the commands the
publish flow runs, keeps the bucket marker in memory, records every command, and re-runs
``assert_command_allowed`` on each one it receives, so a test fails if the code under test builds a
command the guard would refuse. ``bin/aws`` wraps the same class as an executable for process tests.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Callable, Optional

from publish import AwsResult, DeployConfig, MARKER_KEY, assert_command_allowed

DEPLOY_ARN = "arn:aws:iam::123456789012:user/rosart-web-deploy"
INVALIDATION_ID = "I2EXAMPLE"
NO_SUCH_KEY = "An error occurred (NoSuchKey) when calling the GetObject operation: The specified key does not exist."
ACCESS_DENIED = "An error occurred (AccessDenied) when calling the operation"


class FakeAws:
    """Scripted answers; every knob is a constructor argument.

    ``fail_passes``: real (non dry-run) sync numbers, counted from 1, that exit 1.
    ``fail``: a predicate over the whole command for any other injected failure.
    ``get_error``: an error code that makes ``get-object`` fail instead of answering.
    ``wait_exit``: the exit code of ``cloudfront wait`` (255 is the CLI's waiter failure).
    ``no_deletes``: the bucket already mirrors ``dist/``, so no ``--delete`` pass lists a deletion.
    ``extra_deletes``: further bucket keys the root ``--delete`` passes list (the bucket changed under us).
    """

    def __init__(self, config: DeployConfig, *, identity_arn: str = DEPLOY_ARN,
                 marker: Optional[dict] = None, get_error: Optional[str] = None, wait_exit: int = 0,
                 fail_passes: Optional[set[int]] = None, fail: Callable[[list[str]], bool] = lambda c: False,
                 temp_dir: Optional[Path] = None, no_deletes: bool = False,
                 extra_deletes: tuple[str, ...] = ()) -> None:
        self.config = config
        self.identity_arn = identity_arn
        self.marker = marker
        self.get_error = get_error
        self.wait_exit = wait_exit
        self.fail_passes = set(fail_passes or ())
        self.fail = fail
        self.temp_dir = temp_dir
        self.no_deletes = no_deletes
        self.extra_deletes = tuple(extra_deletes)
        self.commands: list[list[str]] = []
        self.marker_writes: list[dict] = []
        self.real_syncs = 0

    def __call__(self, command: list[str]) -> AwsResult:
        assert_command_allowed(command, self.config, self.temp_dir or self._local_folder(command))
        self.commands.append(list(command))
        if self.fail(command):
            return AwsResult(1, "", ACCESS_DENIED)
        args = command[1:]
        if args[:2] == ["sts", "get-caller-identity"]:
            return AwsResult(0, json.dumps({"UserId": "AIDAEXAMPLE", "Account": "123456789012",
                                            "Arn": self.identity_arn}), "")
        if args[:2] == ["s3", "sync"]:
            return self._sync(command)
        if args[:2] == ["s3api", "get-object"]:
            return self._get_object(command)
        if args[:2] == ["s3api", "put-object"]:
            return self._put_object(command)
        if args[:2] == ["cloudfront", "create-invalidation"]:
            return AwsResult(0, json.dumps({"Invalidation": {"Id": INVALIDATION_ID, "Status": "InProgress"}}), "")
        if args[:3] == ["cloudfront", "wait", "invalidation-completed"]:
            return AwsResult(self.wait_exit, "", "Waiter InvalidationCompleted failed" if self.wait_exit else "")
        return AwsResult(0, "", "")

    # --- answers ---

    def _sync(self, command: list[str]) -> AwsResult:
        dryrun = "--dryrun" in command
        if not dryrun:
            self.real_syncs += 1
            if self.real_syncs in self.fail_passes:
                return AwsResult(1, "", ACCESS_DENIED)
        source, dest = command[3], command[4]
        if dest.endswith("/_astro"):
            lines = [f"upload: {source}\\new.Ab12.css to {dest}/new.Ab12.css"]
            if "--delete" in command and not self.no_deletes:
                lines.append(f"delete: {dest}/old.Zz99.css")
        else:
            lines = [f"upload: {source}\\index.html to {dest}/index.html"]
            if "--delete" in command and not self.no_deletes:
                lines.append(f"delete: {dest}/old-page/index.html")
                lines.extend(f"delete: {dest}/{key}" for key in self.extra_deletes)
        prefix = "(dryrun) " if dryrun else ""
        return AwsResult(0, "".join(f"{prefix}{line}\r\n" for line in lines), "")

    def _get_object(self, command: list[str]) -> AwsResult:
        if self.get_error:
            return AwsResult(254, "", f"An error occurred ({self.get_error}) when calling the GetObject operation")
        if self.marker is None:
            return AwsResult(254, "", NO_SUCH_KEY)
        Path(self._local_file(command)).write_text(json.dumps(self.marker), encoding="utf-8")
        return AwsResult(0, json.dumps({"ContentType": "application/json"}), "")

    def _put_object(self, command: list[str]) -> AwsResult:
        body = Path(command[command.index("--body") + 1])
        self.marker = json.loads(body.read_text(encoding="utf-8"))
        self.marker_writes.append(self.marker)
        return AwsResult(0, json.dumps({"ETag": "etag"}), "")

    # --- helpers ---

    def ran(self, *words: str) -> list[list[str]]:
        return [c for c in self.commands if c[1:1 + len(words)] == list(words)]

    @staticmethod
    def _local_file(command: list[str]) -> str:
        args = command[1:]
        if args[:2] == ["s3api", "put-object"]:
            return args[args.index("--body") + 1]
        if args[:2] == ["s3api", "get-object"]:
            return next(a for i, a in enumerate(args[2:], start=2)
                        if not a.startswith("--") and not args[i - 1].startswith("--"))
        return ""

    @classmethod
    def _local_folder(cls, command: list[str]) -> Optional[Path]:
        local = cls._local_file(command) if command[1:2] == ["s3api"] else ""
        return Path(local).parent if local else None


__all__ = ["FakeAws", "MARKER_KEY", "DEPLOY_ARN", "INVALIDATION_ID"]
