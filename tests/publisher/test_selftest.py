"""``python -m publisher selftest`` (spec 006 T099/T100; contracts/rosart-web-interfaces.md R-5, plan Phase 5).

The owner (and ``make image-test``) runs it in the final image with a read-only root and no secrets: it reports the
versions of node, npm, aws and git, finds ``Australia/Melbourne`` in tzdata, expects ``/app`` not writable, refuses
to run when a secret variable or the test-only fault hook is present, and exits non-zero on any failed item.

Seam: ``publisher.__main__.selftest`` with its tools, time zone lookup and write probe injected; one process-level
test runs the real command.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

VERSIONS = {
    "node": "v24.22.2",
    "npm": "11.5.1",
    "aws": "aws-cli/2.39.6 Python/3.13.9 Linux/6.8 exe/x86_64.debian.13",
    "git": "git version 2.47.3",
}


def fake_run(overrides=None):
    """A tool runner: argv -> (exit code, output). `overrides` maps a tool to a (code, output) pair."""
    overrides = overrides or {}

    def run(argv):
        tool = argv[0]
        if tool in overrides:
            return overrides[tool]
        assert argv[1:] == ["--version"], f"selftest ran {argv}: only version checks are expected"
        return 0, VERSIONS[tool] + "\n"

    return run


def melbourne_found(name):
    assert name == "Australia/Melbourne"
    return True


def run_selftest(capsys, environ=None, run=None, tz_lookup=melbourne_found, can_write=lambda path: False,
                 app_root=Path("/app")):
    from publisher.__main__ import selftest

    code = selftest(environ={"PATH": "/usr/bin"} if environ is None else environ, app_root=app_root,
                    run=run or fake_run(), tz_lookup=tz_lookup, can_write=can_write)
    return code, capsys.readouterr()


def test_a_healthy_image_reports_every_version_and_exits_zero(capsys):
    code, out = run_selftest(capsys)

    assert code == 0, out.out + out.err
    for value in VERSIONS.values():
        assert value in out.out
    assert "Australia/Melbourne" in out.out
    assert "FAIL" not in out.out + out.err


@pytest.mark.parametrize("tool", sorted(VERSIONS))
def test_a_tool_that_fails_to_report_its_version_fails_the_selftest(capsys, tool):
    code, out = run_selftest(capsys, run=fake_run({tool: (127, "")}))

    assert code != 0
    failed = [line for line in (out.out + out.err).splitlines() if line.startswith("FAIL")]
    assert len(failed) == 1 and tool in failed[0]


def test_a_tool_that_is_not_installed_fails_instead_of_crashing(capsys):
    def run(argv):
        if argv[0] == "aws":
            raise FileNotFoundError("aws")
        return fake_run()(argv)

    code, out = run_selftest(capsys, run=run)

    assert code != 0
    assert any(line.startswith("FAIL") and "aws" in line for line in (out.out + out.err).splitlines())
    assert VERSIONS["git"] in out.out, "the other items are still reported"


def test_missing_tzdata_for_melbourne_fails_the_selftest(capsys):
    code, out = run_selftest(capsys, tz_lookup=lambda name: False)

    assert code != 0
    assert any(line.startswith("FAIL") and "Australia/Melbourne" in line for line in (out.out + out.err).splitlines())


def test_a_writable_app_folder_fails_the_selftest(capsys):
    code, out = run_selftest(capsys, can_write=lambda path: True)

    assert code != 0
    assert any(line.startswith("FAIL") and "/app" in line for line in (out.out + out.err).splitlines())


def test_the_default_write_probe_sees_a_writable_folder_as_writable(capsys, tmp_path):
    from publisher.__main__ import selftest

    code = selftest(environ={"PATH": "/usr/bin"}, app_root=tmp_path, run=fake_run(), tz_lookup=melbourne_found)

    assert code != 0, "a temp folder is writable, so it must not pass as a read-only /app"
    assert list(tmp_path.iterdir()) == [], "the probe must not leave anything behind"


@pytest.mark.parametrize("name", ["ROSART_SHOPIFY_CLIENT_ID", "ROSART_SHOPIFY_CLIENT_SECRET",
                                  "ROSART_SHOPIFY_ACCESS_TOKEN", "ROSART_AWS_ACCESS_KEY_ID",
                                  "ROSART_AWS_SECRET_ACCESS_KEY", "SLACK_BOT_TOKEN", "PUBLISHER_FAULT_HOOK"])
def test_it_refuses_to_run_while_a_secret_or_the_fault_hook_is_set(capsys, name):
    planted = "planted-value-must-never-print"

    code, out = run_selftest(capsys, environ={"PATH": "/usr/bin", name: planted})

    assert code != 0
    text = out.out + out.err
    assert name in text, "name the variable"
    assert planted not in text, "never print its value"
    assert VERSIONS["node"] not in text, "a refused selftest runs nothing else"


def test_an_empty_secret_variable_still_counts_as_present(capsys):
    code, out = run_selftest(capsys, environ={"PATH": "/usr/bin", "SLACK_BOT_TOKEN": ""})

    assert code != 0 and "SLACK_BOT_TOKEN" in out.out + out.err


def test_the_real_command_reports_each_item_and_its_exit_code_matches(tmp_path):
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("ROSART_") and key not in ("SLACK_BOT_TOKEN", "PUBLISHER_FAULT_HOOK")}
    env["PYTHONDONTWRITEBYTECODE"] = "1"

    completed = subprocess.run([sys.executable, "-m", "publisher", "selftest"], cwd=REPO_ROOT, env=env,
                               capture_output=True, text=True, timeout=120)

    lines = (completed.stdout + completed.stderr).splitlines()
    for item in ("node", "npm", "aws", "git", "Australia/Melbourne", "/app"):
        assert any(item in line for line in lines), f"no line about {item}: {lines}"
    failed = any(line.startswith("FAIL") for line in lines)
    assert (completed.returncode != 0) == failed
