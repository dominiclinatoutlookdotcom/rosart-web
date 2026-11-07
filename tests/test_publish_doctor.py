"""Tests for the publish preflight (`make doctor`) with a fake system: no network, no AWS, no Shopify.

The fake answers each command the doctor runs from a table, and plays the Shopify CLI by writing
its result to the ``--output-file`` the doctor names, like the real CLI does.
"""
import json
import subprocess
from pathlib import Path
from typing import Optional

import pytest

from publish_doctor import CommandResult, DoctorEnv, main, run_checks

STORE = "rosart-mhfmyuxq.myshopify.com"
THEME_ID = "201570156625"
READ_SCOPES = ["read_content", "read_products", "read_online_store_navigation", "read_files",
               "read_themes", "read_metaobjects", "read_legal_policies"]
CONFIG = {"profile": "rosart-deploy", "bucket": "rosart-web-site", "region": "ap-southeast-2",
          "distributionId": "E5L4UOPZMBQHP"}


def ok(stdout: str = "", stderr: str = "") -> CommandResult:
    return CommandResult(0, stdout, stderr)


def failed(stderr: str, returncode: int = 1) -> CommandResult:
    return CommandResult(returncode, "", stderr)


class FakeSystem:
    """Every tool installed and working, the repo clean on main and level with origin."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.paths = {name: f"/bin/{name}" for name in
                      ("git", "node", "npm", "python", "make", "shopify", "aws")}
        self.answers: dict[tuple[str, ...], CommandResult] = {
            ("git", "--version"): ok("git version 2.48.1.windows.1\n"),
            ("node", "--version"): ok("v24.13.0\n"),
            ("npm", "--version"): ok("11.6.2\n"),
            ("python", "--version"): ok("Python 3.14.2\n"),
            ("make", "--version"): ok("GNU Make 4.4.1\nBuilt for x86_64-pc-msys\n"),
            ("shopify", "version"): ok("\n4.9.4\n"),
            ("aws", "--version"): ok("aws-cli/2.13.32 Python/3.11.6 Windows/10 exe/AMD64 prompt/off\n"),
            ("git", "rev-parse", "--show-toplevel"): ok(root.as_posix() + "\n"),
            ("git", "rev-parse", "--abbrev-ref", "HEAD"): ok("main\n"),
            ("git", "status", "--porcelain"): ok(""),
            ("git", "remote"): ok("origin\n"),
            ("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"): ok("origin/main\n"),
            ("git", "fetch", "--quiet", "origin"): ok(),
            ("git", "rev-list", "--left-right", "--count", "HEAD...@{u}"): ok("0\t0\n"),
            ("git", "config", "user.name"): ok("Staff Member\n"),
            ("git", "config", "user.email"): ok("staff@rosart.com.au\n"),
            ("aws", "sts", "get-caller-identity"): ok(json.dumps(
                {"UserId": "AIDAEXAMPLE", "Account": "123456789012",
                 "Arn": "arn:aws:iam::123456789012:user/rosart-deploy"})),
        }
        self.commands: list[list[str]] = []
        self.scopes: Optional[list[str]] = list(READ_SCOPES)
        self.store_error: Optional[str] = None
        self.themes: Optional[list[dict]] = [{"id": int(THEME_ID), "name": "Horizon", "role": "live"}]
        self.theme_error: Optional[str] = None
        self.shopify_calls: list[dict] = []

    # --- the three seams the doctor takes ---

    def which(self, name: str) -> Optional[str]:
        return self.paths.get(name)

    def run(self, command: list[str], timeout: int = 60) -> CommandResult:
        self.commands.append(command)
        for length in range(len(command), 0, -1):
            answer = self.answers.get(tuple(command[:length]))
            if answer is not None:
                return answer
        raise AssertionError(f"unexpected command: {command}")

    def shopify_process(self, command, cwd=None, stdout=None, stderr=None, stdin=None, timeout=None):
        self.shopify_calls.append({"command": command, "stdout": stdout, "stdin": stdin})
        if command[1:3] == ["store", "execute"]:
            if self.store_error:
                stderr.write(self.store_error.encode("utf-8"))
                return subprocess.CompletedProcess(command, 1)
            result = {"currentAppInstallation": {"accessScopes": [{"handle": s} for s in self.scopes]},
                      "shop": {"name": "Rosart – Bulgarian Rose"}}
            out = Path(command[command.index("--output-file") + 1])
            out.write_bytes(json.dumps(result, ensure_ascii=False).encode("utf-8"))
            return subprocess.CompletedProcess(command, 0)
        if command[1:3] == ["theme", "list"]:
            if self.theme_error:
                stderr.write(self.theme_error.encode("utf-8"))
                return subprocess.CompletedProcess(command, 1)
            stdout.write(json.dumps(self.themes).encode("utf-8"))
            return subprocess.CompletedProcess(command, 0)
        raise AssertionError(f"unexpected shopify command: {command}")


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "package.json").write_text(json.dumps({"name": "rosart-web", "engines": {"node": ">=24"}}),
                                           encoding="utf-8")
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    (tmp_path / "deploy.config.json").write_text(json.dumps(CONFIG), encoding="utf-8")
    modules = tmp_path / "node_modules"
    modules.mkdir()
    (modules / ".package-lock.json").write_text("{}", encoding="utf-8")
    return tmp_path


@pytest.fixture
def system(repo: Path) -> FakeSystem:
    return FakeSystem(repo)


def env_for(system: FakeSystem, platform: str = "win32", cwd: Optional[Path] = None) -> DoctorEnv:
    return DoctorEnv(repo_root=system.root, cwd=cwd or system.root, run=system.run, which=system.which,
                     shopify_process=system.shopify_process, platform=platform,
                     environ={"USERPROFILE": "C:/Users/staff", "HOME": "C:/Users/staff"})


def checks(system: FakeSystem, **kwargs) -> dict:
    return {c.name: c for c in run_checks(env_for(system, **kwargs))}


# --- the report ---------------------------------------------------------------------------

def test_everything_ready_passes_every_check_and_exits_zero(system, capsys):
    assert main([], env=env_for(system)) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    status_lines = [line for line in lines if line.split(" ", 1)[0] in ("PASS", "FAIL", "WARN")]
    assert status_lines and all(line.startswith("PASS ") for line in status_lines)
    assert not any("fix:" in line for line in lines)


def test_a_fail_prints_a_fix_line_under_it_and_exits_one(system, capsys):
    system.answers[("node", "--version")] = ok("v22.1.0\n")
    assert main([], env=env_for(system)) == 1
    lines = capsys.readouterr().out.splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("FAIL node:"))
    assert lines[index + 1].strip().startswith("fix: ")


def test_a_warning_alone_still_exits_zero(system, capsys):
    system.answers[("git", "remote")] = ok("")
    assert main([], env=env_for(system)) == 0
    out = capsys.readouterr().out
    assert "WARN remote:" in out and "fix: " in out


# --- tools --------------------------------------------------------------------------------

def test_tool_versions_are_reported(system):
    result = checks(system)
    assert result["git"].status == "PASS" and "2.48.1" in result["git"].detail
    assert result["node"].status == "PASS" and "24.13.0" in result["node"].detail
    assert result["python"].status == "PASS" and "3.14.2" in result["python"].detail
    assert result["make"].status == "PASS" and "4.4.1" in result["make"].detail
    assert result["shopify-cli"].status == "PASS" and "4.9.4" in result["shopify-cli"].detail
    assert result["aws-cli"].status == "PASS" and "2.13.32" in result["aws-cli"].detail


def test_node_older_than_package_engines_fails(system):
    system.answers[("node", "--version")] = ok("v22.11.0\n")
    node = checks(system)["node"]
    assert node.status == "FAIL" and "24" in node.detail
    assert "OpenJS.NodeJS" in node.fix


def test_node_minimum_is_20_without_engines(system, repo):
    (repo / "package.json").write_text(json.dumps({"name": "rosart-web"}), encoding="utf-8")
    system.answers[("node", "--version")] = ok("v20.0.0\n")
    assert checks(system)["node"].status == "PASS"
    system.answers[("node", "--version")] = ok("v18.20.0\n")
    assert checks(system)["node"].status == "FAIL"


def test_missing_tool_fails_with_a_windows_install_route(system):
    del system.paths["make"]
    make = checks(system)["make"]
    assert make.status == "FAIL" and "not found" in make.detail
    assert "winget install" in make.fix and "ezwinports.make" in make.fix


def test_missing_tool_on_another_platform_points_at_the_setup_guide(system):
    del system.paths["aws"]
    aws = checks(system, platform="darwin")["aws-cli"]
    assert aws.status == "FAIL" and "docs/staff-setup.md" in aws.fix


def test_make_must_be_gnu_make(system):
    system.answers[("make", "--version")] = ok("bmake 20240711\n")
    assert checks(system)["make"].status == "FAIL"


def test_aws_cli_version_1_fails(system):
    system.answers[("aws", "--version")] = ok("aws-cli/1.29.0 Python/3.11 Windows/10 botocore/1.31\n")
    aws = checks(system)["aws-cli"]
    assert aws.status == "FAIL" and "v2" in aws.fix


def test_old_python_fails(system):
    system.answers[("python", "--version")] = ok("Python 3.8.10\n")
    assert checks(system)["python"].status == "FAIL"


def test_a_tool_that_errors_fails_instead_of_crashing(system):
    system.answers[("shopify", "version")] = failed("boom")
    assert checks(system)["shopify-cli"].status == "FAIL"


# --- the repo -----------------------------------------------------------------------------

def test_clean_main_level_with_origin_passes(system):
    result = checks(system)
    for name in ("repo", "branch", "working-tree", "remote"):
        assert result[name].status == "PASS", result[name]


def test_running_outside_the_repo_fails(system, tmp_path_factory):
    elsewhere = tmp_path_factory.mktemp("elsewhere")
    repo_check = checks(system, cwd=elsewhere)["repo"]
    assert repo_check.status == "FAIL" and "cd " in repo_check.fix


def test_a_different_git_repo_fails(system, repo):
    (repo / "package.json").write_text(json.dumps({"name": "something-else"}), encoding="utf-8")
    assert checks(system)["repo"].status == "FAIL"


def test_not_a_git_checkout_fails(system):
    system.answers[("git", "rev-parse", "--show-toplevel")] = failed("fatal: not a git repository")
    assert checks(system)["repo"].status == "FAIL"


def test_another_branch_fails_with_switch_fix(system):
    system.answers[("git", "rev-parse", "--abbrev-ref", "HEAD")] = ok("seo-fixes\n")
    branch = checks(system)["branch"]
    assert branch.status == "FAIL" and "seo-fixes" in branch.detail
    assert "git switch main" in branch.fix


def test_changes_outside_export_fail_and_name_the_files(system):
    system.answers[("git", "status", "--porcelain")] = ok(" M src/site.config.ts\n?? notes.txt\n M export/shopify/pages.json\n")
    tree = checks(system)["working-tree"]
    assert tree.status == "FAIL"
    assert "src/site.config.ts" in tree.detail and "notes.txt" in tree.detail
    assert "owner" in tree.fix and "git restore" not in tree.fix


def test_changes_only_in_export_fail_with_the_reset_fix(system):
    system.answers[("git", "status", "--porcelain")] = ok(" M export/shopify/pages.json\n?? export/images/new.jpg\n")
    tree = checks(system)["working-tree"]
    assert tree.status == "FAIL" and "export/shopify/pages.json" in tree.detail
    assert "git restore --staged --worktree export" in tree.fix and "git clean -fd export" in tree.fix


def test_no_remote_is_a_warning(system):
    system.answers[("git", "remote")] = ok("")
    remote = checks(system)["remote"]
    assert remote.status == "WARN" and "no remote" in remote.detail


def test_no_upstream_is_a_warning(system):
    system.answers[("git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")] = failed(
        "fatal: no upstream configured for branch 'main'")
    remote = checks(system)["remote"]
    assert remote.status == "WARN" and "git branch --set-upstream-to=origin/main" in remote.fix


def test_behind_the_remote_fails_with_pull(system):
    system.answers[("git", "rev-list", "--left-right", "--count", "HEAD...@{u}")] = ok("0\t3\n")
    remote = checks(system)["remote"]
    assert remote.status == "FAIL" and "3" in remote.detail
    assert remote.fix == "git pull --ff-only"


def test_ahead_of_the_remote_is_a_warning_for_the_owner_to_push(system):
    system.answers[("git", "rev-list", "--left-right", "--count", "HEAD...@{u}")] = ok("2\t0\n")
    remote = checks(system)["remote"]
    assert remote.status == "WARN" and "2" in remote.detail
    assert "owner" in remote.fix and "never push" in remote.fix


def test_diverged_fails_and_sends_to_the_owner(system):
    system.answers[("git", "rev-list", "--left-right", "--count", "HEAD...@{u}")] = ok("1\t2\n")
    remote = checks(system)["remote"]
    assert remote.status == "FAIL" and "owner" in remote.fix


def test_unreachable_remote_warns_and_still_compares_with_the_last_fetch(system):
    system.answers[("git", "fetch", "--quiet", "origin")] = failed("ssh: Could not resolve hostname github.com")
    remote = checks(system)["remote"]
    assert remote.status == "WARN" and "last fetch" in remote.detail


def test_repo_checks_are_skipped_without_git(system):
    del system.paths["git"]
    result = checks(system)
    assert result["git"].status == "FAIL"
    assert result["branch"].status == "WARN" and "skipped" in result["branch"].detail
    assert not any(cmd[0] == "git" for cmd in system.commands)


# --- dependencies, deploy config, AWS -----------------------------------------------------

def test_missing_node_modules_fails_with_npm_ci(system, repo):
    (repo / "node_modules" / ".package-lock.json").unlink()
    modules = checks(system)["node-modules"]
    assert modules.status == "FAIL" and modules.fix == "npm ci"


def test_node_modules_older_than_the_lockfile_warns(system, repo):
    import os
    hidden = repo / "node_modules" / ".package-lock.json"
    os.utime(hidden, (1_000_000_000, 1_000_000_000))
    modules = checks(system)["node-modules"]
    assert modules.status == "WARN" and modules.fix == "npm ci"


def test_filled_deploy_config_passes(system):
    config = checks(system)["deploy-config"]
    assert config.status == "PASS" and "rosart-web-site" in config.detail


def test_placeholder_deploy_config_fails_and_sends_to_the_owner(system, repo):
    (repo / "deploy.config.json").write_text(json.dumps({**CONFIG, "distributionId": "REPLACE_ME"}),
                                             encoding="utf-8")
    result = checks(system)
    assert result["deploy-config"].status == "FAIL" and "distributionId" in result["deploy-config"].detail
    assert "owner" in result["deploy-config"].fix
    assert result["aws-profile"].status == "WARN" and "skipped" in result["aws-profile"].detail


def test_aws_profile_shows_the_identity(system):
    aws = checks(system)["aws-profile"]
    assert aws.status == "PASS"
    assert "123456789012" in aws.detail and "user/rosart-deploy" in aws.detail


def test_aws_identity_uses_the_profile_and_region_from_the_config(system):
    checks(system)
    sts = [cmd for cmd in system.commands if cmd[:3] == ["aws", "sts", "get-caller-identity"]]
    assert len(sts) == 1
    command = sts[0]
    assert command[command.index("--profile") + 1] == "rosart-deploy"
    assert command[command.index("--region") + 1] == "ap-southeast-2"


def test_missing_aws_profile_fails_with_aws_configure(system):
    system.answers[("aws", "sts", "get-caller-identity")] = failed(
        "\nThe config profile (rosart-deploy) could not be found\n", returncode=255)
    aws = checks(system)["aws-profile"]
    assert aws.status == "FAIL" and "not set up" in aws.detail
    assert "aws configure --profile rosart-deploy" in aws.fix
    assert "ap-southeast-2" in aws.fix and "owner" in aws.fix


def test_rejected_aws_key_fails_with_aws_configure(system):
    system.answers[("aws", "sts", "get-caller-identity")] = failed(
        "An error occurred (InvalidClientTokenId) when calling the GetCallerIdentity operation: "
        "The security token included in the request is invalid.", returncode=254)
    aws = checks(system)["aws-profile"]
    assert aws.status == "FAIL" and "rejected" in aws.detail
    assert "aws configure --profile rosart-deploy" in aws.fix


def test_aws_offline_fails_with_a_connection_hint(system):
    system.answers[("aws", "sts", "get-caller-identity")] = failed(
        'Could not connect to the endpoint URL: "https://sts.ap-southeast-2.amazonaws.com/"', returncode=255)
    aws = checks(system)["aws-profile"]
    assert aws.status == "FAIL" and "internet" in aws.fix


def test_aws_profile_is_skipped_without_the_aws_cli(system):
    del system.paths["aws"]
    assert checks(system)["aws-profile"].status == "WARN"
    assert not any(cmd[0] == "aws" for cmd in system.commands)


# --- Shopify ------------------------------------------------------------------------------

AUTH_FIX = ("shopify store auth --store rosart-mhfmyuxq.myshopify.com --scopes "
            "read_content,read_products,read_online_store_navigation,read_files,read_themes,"
            "read_metaobjects,read_legal_policies")


def test_store_auth_with_every_read_scope_passes(system):
    auth = checks(system)["shopify-store-auth"]
    assert auth.status == "PASS" and STORE in auth.detail


def test_store_scopes_are_read_from_the_output_file_never_a_stdout_pipe(system):
    checks(system)
    call = next(c for c in system.shopify_calls if c["command"][1:3] == ["store", "execute"])
    command = call["command"]
    assert "--output-file" in command and command[command.index("--store") + 1] == STORE
    assert "--allow-mutations" not in command
    assert call["stdout"] is not subprocess.PIPE and hasattr(call["stdout"], "write")
    assert call["stdin"] is subprocess.DEVNULL


def test_no_store_auth_fails_with_the_exact_read_only_auth_command(system):
    system.store_error = "No stored app authentication found for rosart-mhfmyuxq.myshopify.com."
    auth = checks(system)["shopify-store-auth"]
    assert auth.status == "FAIL"
    assert AUTH_FIX in auth.fix and "write_" not in auth.fix


def test_a_missing_read_scope_fails_and_names_it(system):
    system.scopes = [s for s in READ_SCOPES if s != "read_files"]
    auth = checks(system)["shopify-store-auth"]
    assert auth.status == "FAIL" and "read_files" in auth.detail
    assert AUTH_FIX in auth.fix


def test_a_write_scope_covers_its_read_scope(system):
    system.scopes = [s for s in READ_SCOPES if s != "read_products"] + ["write_products"]
    assert checks(system)["shopify-store-auth"].status == "PASS"


def test_missing_legal_policies_scope_only_warns(system):
    system.scopes = [s for s in READ_SCOPES if s != "read_legal_policies"]
    auth = checks(system)["shopify-store-auth"]
    assert auth.status == "WARN" and "read_legal_policies" in auth.detail
    assert AUTH_FIX in auth.fix


def test_theme_access_passes_when_the_theme_is_listed(system):
    theme = checks(system)["shopify-theme-access"]
    assert theme.status == "PASS" and THEME_ID in theme.detail
    call = next(c for c in system.shopify_calls if c["command"][1:3] == ["theme", "list"])
    assert "--no-input" in call["command"] and call["stdout"] is not subprocess.PIPE


def test_theme_access_without_a_login_fails_with_auth_login(system):
    system.theme_error = "Please log in: run shopify auth login"
    theme = checks(system)["shopify-theme-access"]
    assert theme.status == "FAIL" and "shopify auth login" in theme.fix


def test_theme_missing_from_the_list_fails(system):
    system.themes = [{"id": 1, "name": "Dawn", "role": "unpublished"}]
    theme = checks(system)["shopify-theme-access"]
    assert theme.status == "FAIL" and THEME_ID in theme.detail and "owner" in theme.fix


def test_shopify_checks_are_skipped_without_the_cli(system):
    del system.paths["shopify"]
    result = checks(system)
    assert result["shopify-store-auth"].status == "WARN"
    assert result["shopify-theme-access"].status == "WARN"
    assert system.shopify_calls == []


def test_check_order_is_the_fix_order(system):
    names = [c.name for c in run_checks(env_for(system))]
    assert names == ["git", "node", "npm", "python", "make", "shopify-cli", "aws-cli",
                     "repo", "branch", "working-tree", "remote", "git-identity", "node-modules", "deploy-config",
                     "aws-profile", "shopify-store-auth", "shopify-theme-access"]


def test_git_identity_names_who_publishes(system):
    identity = checks(system)["git-identity"]
    assert identity.status == "PASS" and "Staff Member" in identity.detail


def test_missing_git_name_fails_with_git_config(system):
    system.answers[("git", "config", "user.name")] = failed("")
    identity = checks(system)["git-identity"]
    assert identity.status == "FAIL"
    assert 'git config --global user.name "Your Name"' in identity.fix


def test_missing_windows_user_profile_is_named_as_the_cause(system):
    # MSYS2 make started from Git Bash drops USERPROFILE, so the AWS CLI looks in the wrong home.
    system.answers[("aws", "sts", "get-caller-identity")] = failed(
        "\nThe config profile (rosart-deploy) could not be found\n", returncode=255)
    env = env_for(system)
    env.environ = {"HOME": "C:/msys64/home/staff"}
    aws = {c.name: c for c in run_checks(env)}["aws-profile"]
    assert aws.status == "FAIL" and "USERPROFILE" in aws.detail
    assert "ezwinports.make" in aws.fix and "aws configure" not in aws.fix


def test_unreachable_remote_says_why(system):
    system.answers[("git", "fetch", "--quiet", "origin")] = failed(
        "git@github.com: Permission denied (publickey).\nfatal: Could not read from remote repository.")
    remote = checks(system)["remote"]
    assert remote.status == "WARN" and "Permission denied (publickey)" in remote.detail
