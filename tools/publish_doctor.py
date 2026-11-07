"""Publish preflight: is this PC ready to run the publish flow? (``make doctor``)

Prints one line per check, ``PASS|FAIL|WARN <name>: <detail>``, with a ``fix:`` line under each
FAIL and WARN, in the order they should be fixed. Exits 0 only when nothing FAILs. It changes
nothing: it reads versions, git state and config, asks AWS who the deploy profile is
(``sts get-caller-identity``), and runs read-only Shopify CLI commands (a scopes query and a
theme list). Shopify CLI results are read from files (``--output-file`` or a redirected stdout)
decoded as UTF-8, never from a stdout pipe: on Windows a pipe mis-decodes them.

Usage::

    python tools/publish_doctor.py
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Optional

from publish import Aws, AwsResult, DeployConfig, PublishError, load_config
from pull_theme import DEFAULT_STORE, DEFAULT_THEME
from shopify_cli import ShopifyCliError, ShopifyCliFileRunner

REPO_ROOT = Path(__file__).resolve().parent.parent
SETUP_GUIDE = "docs/staff-setup.md"


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class Check:
    status: str  # PASS, FAIL or WARN
    name: str
    detail: str
    fix: Optional[str] = None


@dataclass
class DoctorEnv:
    """Everything the doctor touches, so tests can pass fakes.

    ``run`` runs ``[program, *args]`` in ``repo_root`` and captures its output (used for plain
    ASCII output only: versions, git, aws); ``which`` finds a program; ``shopify_process`` is a
    ``subprocess.run``-compatible callable for the Shopify CLI, whose output goes to files;
    ``environ`` is the environment the child programs inherit.
    """
    repo_root: Path
    cwd: Path
    run: Callable[[list[str]], CommandResult]
    which: Callable[[str], Optional[str]]
    shopify_process: Callable[..., Any]
    platform: str = sys.platform
    environ: Mapping[str, str] = field(default_factory=lambda: dict(os.environ))


def _pass(name: str, detail: str) -> Check:
    return Check("PASS", name, detail)


def _fail(name: str, detail: str, fix: str) -> Check:
    return Check("FAIL", name, detail, fix)


def _warn(name: str, detail: str, fix: str) -> Check:
    return Check("WARN", name, detail, fix)


# --- tools --------------------------------------------------------------------------------

@dataclass(frozen=True)
class Tool:
    name: str        # check name
    program: str     # executable on PATH
    args: tuple[str, ...]
    pattern: str     # regex over the version output; group 1 is the version
    windows_fix: str


NEW_TERMINAL = "then close and reopen the terminal (and Claude Code) so it is on PATH"

TOOLS = (
    Tool("git", "git", ("--version",), r"git version (\d+\.\d+\.\d+)",
         "winget install --id Git.Git -e"),
    Tool("node", "node", ("--version",), r"v(\d+\.\d+\.\d+)",
         "winget install --id OpenJS.NodeJS.LTS -e"),
    Tool("npm", "npm", ("--version",), r"(\d+\.\d+\.\d+)",
         "npm comes with Node.js: winget install --id OpenJS.NodeJS.LTS -e"),
    Tool("python", "python", ("--version",), r"Python (\d+\.\d+\.\d+)",
         "winget install --id Python.Python.3.13 -e"),
    Tool("make", "make", ("--version",), r"GNU Make (\d+\.\d+(?:\.\d+)?)",
         "winget install --id ezwinports.make -e"),
    Tool("shopify-cli", "shopify", ("version",), r"(\d+\.\d+\.\d+)",
         "npm install -g @shopify/cli@latest"),
    Tool("aws-cli", "aws", ("--version",), r"aws-cli/(\d+\.\d+\.\d+)",
         "winget install --id Amazon.AWSCLI -e (AWS CLI v2)"),
)

MIN_PYTHON = (3, 10)
DEFAULT_MIN_NODE = 20


def _version_tuple(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def _install_fix(tool: Tool, platform: str) -> str:
    if platform == "win32":
        return f"{tool.windows_fix}; {NEW_TERMINAL}"
    return f"install {tool.program} (see {SETUP_GUIDE}), then open a new terminal"


def _min_node(repo_root: Path) -> int:
    """The major version package.json's ``engines.node`` asks for, else 20."""
    try:
        package = json.loads((repo_root / "package.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_MIN_NODE
    wanted = str((package.get("engines") or {}).get("node") or "")
    match = re.search(r"(\d+)", wanted)
    return int(match.group(1)) if match else DEFAULT_MIN_NODE


def _run(env: DoctorEnv, command: list[str], timeout: int = 60) -> CommandResult:
    try:
        return env.run(command, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return CommandResult(127, "", str(exc))


def check_tool(env: DoctorEnv, tool: Tool) -> Check:
    if not env.which(tool.program):
        return _fail(tool.name, f"{tool.program} not found on PATH", _install_fix(tool, env.platform))
    result = _run(env, [tool.program, *tool.args])
    output = (result.stdout + "\n" + result.stderr).strip()
    match = re.search(tool.pattern, output)
    if result.returncode != 0 or not match:
        first = output.splitlines()[0] if output else f"exit {result.returncode}"
        expected = "GNU Make" if tool.name == "make" else "a version"
        return _fail(tool.name, f"`{tool.program} {' '.join(tool.args)}` did not report {expected}: {first[:200]}",
                     _install_fix(tool, env.platform))
    version = match.group(1)
    numbers = _version_tuple(version)

    if tool.name == "node":
        minimum = _min_node(env.repo_root)
        if numbers[0] < minimum:
            return _fail("node", f"{version} is older than the {minimum} this site needs (package.json engines)",
                         _install_fix(tool, env.platform))
    if tool.name == "python" and numbers[:2] < MIN_PYTHON:
        return _fail("python", f"{version} is older than {'.'.join(map(str, MIN_PYTHON))}",
                     _install_fix(tool, env.platform))
    if tool.name == "aws-cli" and numbers[0] < 2:
        return _fail("aws-cli", f"{version} is AWS CLI v1; publishing needs v2",
                     f"uninstall AWS CLI v1, then install AWS CLI v2: {_install_fix(tool, env.platform)}")
    return _pass(tool.name, f"{version} ({env.which(tool.program)})")


# --- the repo -----------------------------------------------------------------------------

PROJECT_NAME = "rosart-web"
BRANCH = "main"
EXPORT_DIR = "export"
FETCH_TIMEOUT = 45


def _skipped(name: str, needs: str) -> Check:
    return _warn(name, f"skipped (needs {needs})", f"fix the {needs} FAIL above, then run make doctor again")


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def check_repo(env: DoctorEnv) -> Check:
    go_there = f"cd {env.repo_root.as_posix()} (the rosart-web clone), then run make doctor again"
    if not _is_within(env.cwd, env.repo_root):
        return _fail("repo", f"the current folder {env.cwd} is not inside the rosart-web repo", go_there)
    result = _run(env, ["git", "rev-parse", "--show-toplevel"])
    if result.returncode != 0:
        return _fail("repo", f"{env.repo_root} is not a git checkout: {result.stderr.strip()[:200]}",
                     f"clone the repo as described in {SETUP_GUIDE}")
    top = Path(result.stdout.strip())
    try:
        same = top.resolve() == env.repo_root.resolve()
        name = json.loads((env.repo_root / "package.json").read_text(encoding="utf-8")).get("name")
    except (OSError, json.JSONDecodeError):
        same, name = False, None
    if not same or name != PROJECT_NAME:
        return _fail("repo", f"{top} is not the {PROJECT_NAME} repo (package.json name {name!r})", go_there)
    return _pass("repo", f"{PROJECT_NAME} at {env.repo_root}")


def check_branch(env: DoctorEnv) -> Check:
    result = _run(env, ["git", "rev-parse", "--abbrev-ref", "HEAD"])
    branch = result.stdout.strip()
    if result.returncode != 0 or not branch:
        return _fail("branch", f"cannot read the current branch: {result.stderr.strip()[:200]}",
                     "contact the owner")
    if branch != BRANCH:
        return _fail("branch", f"on branch {branch}, publishing is from {BRANCH}",
                     f"git switch {BRANCH} (if git refuses because of changes, stop and contact the owner)")
    return _pass("branch", BRANCH)


def _status_path(line: str) -> str:
    path = line[3:]
    return path.split(" -> ", 1)[-1].strip().strip('"')


def check_working_tree(env: DoctorEnv) -> Check:
    result = _run(env, ["git", "status", "--porcelain"])
    if result.returncode != 0:
        return _fail("working-tree", f"git status failed: {result.stderr.strip()[:200]}", "contact the owner")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return _pass("working-tree", "clean")
    shown = ", ".join(line.strip() for line in lines[:10]) + (f" (and {len(lines) - 10} more)" if len(lines) > 10 else "")
    detail = f"uncommitted changes: {shown}"
    if all(_status_path(line) == EXPORT_DIR or _status_path(line).startswith(EXPORT_DIR + "/") for line in lines):
        return _fail("working-tree", detail,
                     "these are from an export that was not published and committed; the next export "
                     "makes them again, so drop them: git restore --staged --worktree export && "
                     "git clean -fd export")
    return _fail("working-tree", detail,
                 "these are not publishing changes: do not discard or commit them; stop and contact the owner")


def check_remote(env: DoctorEnv) -> Check:
    remotes = _run(env, ["git", "remote"]).stdout.split()
    if not remotes:
        return _warn("remote", "no remote configured yet: cannot tell whether this copy is up to date",
                     "nothing to do for publishing; the owner sets up the remote")
    upstream = _run(env, ["git", "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}"])
    if upstream.returncode != 0 or not upstream.stdout.strip():
        return _warn("remote", f"{BRANCH} does not track a remote branch",
                     f"git branch --set-upstream-to={remotes[0]}/{BRANCH} {BRANCH}")
    tracking = upstream.stdout.strip()
    remote = tracking.split("/", 1)[0] if "/" in tracking else remotes[0]
    fetched = _run(env, ["git", "fetch", "--quiet", remote], timeout=FETCH_TIMEOUT)
    reason = (fetched.stderr.strip().splitlines() or [f"exit {fetched.returncode}"])[0][:200]
    stale = "" if fetched.returncode == 0 else f" (as of the last fetch: could not reach the remote: {reason})"
    counts = _run(env, ["git", "rev-list", "--left-right", "--count", "HEAD...@{u}"])
    try:
        ahead, behind = (int(n) for n in counts.stdout.split())
    except ValueError:
        return _warn("remote", f"cannot compare with {tracking}: {counts.stderr.strip()[:200]}",
                     "contact the owner")
    if ahead and behind:
        return _fail("remote", f"{ahead} local and {behind} remote commits differ from {tracking}{stale}",
                     "stop and contact the owner (the copies have diverged; do not pull or push)")
    if behind:
        return _fail("remote", f"{behind} commit(s) behind {tracking}{stale}", "git pull --ff-only")
    if ahead:
        return _warn("remote", f"{ahead} local commit(s) not on {tracking} yet{stale}",
                     "tell the owner, who pushes them; never push")
    if stale:
        return _warn("remote", f"level with {tracking}{stale}",
                     "check the internet connection and your GitHub access; if it keeps failing, tell the owner")
    return _pass("remote", f"level with {tracking}")


def check_git_identity(env: DoctorEnv) -> Check:
    """The publish commit is signed with the staff member's git name."""
    name = _run(env, ["git", "config", "user.name"]).stdout.strip()
    email = _run(env, ["git", "config", "user.email"]).stdout.strip()
    if not name or not email:
        return _fail("git-identity", "git does not know your name and email (the publish commit needs them)",
                     'git config --global user.name "Your Name" and '
                     'git config --global user.email "you@rosart.com.au"')
    return _pass("git-identity", f"{name} <{email}>")


# --- dependencies, deploy config, AWS -----------------------------------------------------

def check_node_modules(env: DoctorEnv) -> Check:
    hidden = env.repo_root / "node_modules" / ".package-lock.json"
    if not hidden.is_file():
        return _fail("node-modules", "the site's packages are not installed (no node_modules)", "npm ci")
    lockfile = env.repo_root / "package-lock.json"
    if lockfile.is_file() and lockfile.stat().st_mtime > hidden.stat().st_mtime:
        return _warn("node-modules", "package-lock.json changed since the packages were installed", "npm ci")
    return _pass("node-modules", "installed")


def check_deploy_config(env: DoctorEnv) -> tuple[Check, Optional[DeployConfig]]:
    try:
        config = load_config(env.repo_root / "deploy.config.json")
    except PublishError as exc:
        return _fail("deploy-config", str(exc),
                     "deploy.config.json is the owner's: do not edit it; contact the owner"), None
    return _pass("deploy-config", f"profile {config.profile}, s3://{config.bucket} ({config.region}), "
                                  f"CloudFront {config.distribution_id}"), config


def check_aws_profile(env: DoctorEnv, config: DeployConfig) -> Check:
    configure = (f"aws configure --profile {config.profile} (Access Key ID and Secret Access Key: the "
                 f"deploy key the owner gives you privately, never in chat; region {config.region}; "
                 "output json)")

    def runner(command: list[str]) -> AwsResult:
        result = _run(env, command)
        return AwsResult(result.returncode, result.stdout, result.stderr)

    try:
        identity = Aws(config, runner).json(["sts", "get-caller-identity"])
    except PublishError as exc:
        message = str(exc)
        lowered = message.lower()
        if "could not be found" in lowered and env.platform == "win32" and not env.environ.get("USERPROFILE"):
            return _fail("aws-profile", "the AWS CLI cannot find your profile because USERPROFILE is not set "
                         "here (MSYS2 make started from Git Bash drops it), so it looks in the wrong home folder",
                         "use Windows make instead: winget install --id ezwinports.make -e, then make sure "
                         "`where make` lists it first (or run make from the MSYS2 terminal)")
        if "could not be found" in lowered:
            return _fail("aws-profile", f"the AWS profile {config.profile} is not set up on this PC", configure)
        if any(code in message for code in ("InvalidClientTokenId", "SignatureDoesNotMatch",
                                              "security token", "AccessDenied", "ExpiredToken")):
            return _fail("aws-profile", f"AWS rejected the key in profile {config.profile}",
                         f"ask the owner for the current deploy key, then {configure}")
        if "could not connect" in lowered or "timed out" in lowered:
            return _fail("aws-profile", "could not reach AWS", "check the internet connection, then run make doctor again")
        return _fail("aws-profile", message[-300:], configure)
    return _pass("aws-profile", f"account {identity.get('Account')}, {identity.get('Arn')} "
                                f"(profile {config.profile})")


# --- Shopify ------------------------------------------------------------------------------

# The read scopes the export's queries need (owner list, 2026-11-07); staff never get write scopes.
REQUIRED_SCOPES = ("read_content", "read_products", "read_online_store_navigation", "read_files",
                   "read_themes", "read_metaobjects")
OPTIONAL_SCOPES = ("read_legal_policies",)
SCOPES_QUERY = "query PublishDoctorScopes { currentAppInstallation { accessScopes { handle } } }"
SHOPIFY_TIMEOUT = 120
AUTH_COMMAND = (f"shopify store auth --store {DEFAULT_STORE} --scopes "
                + ",".join(REQUIRED_SCOPES + OPTIONAL_SCOPES))
AUTH_FIX = f"{AUTH_COMMAND} (it opens the browser: sign in to Shopify there and approve)"


def check_store_auth(env: DoctorEnv) -> Check:
    runner = ShopifyCliFileRunner(DEFAULT_STORE, executable=env.which("shopify"), timeout=SHOPIFY_TIMEOUT,
                                  run_process=env.shopify_process)
    try:
        data = runner(SCOPES_QUERY, {})
    except (ShopifyCliError, OSError, subprocess.SubprocessError) as exc:
        return _fail("shopify-store-auth", f"the Shopify CLI cannot read {DEFAULT_STORE}: {str(exc)[-300:]}",
                     AUTH_FIX)
    scopes = (data.get("currentAppInstallation") or {}).get("accessScopes") or []
    granted = {s.get("handle") for s in scopes if isinstance(s, dict)}

    def covered(scope: str) -> bool:
        # A write scope includes its read scope.
        return scope in granted or "write_" + scope[len("read_"):] in granted

    missing = [s for s in REQUIRED_SCOPES if not covered(s)]
    if missing:
        return _fail("shopify-store-auth", f"the store sign-in lacks {', '.join(missing)}", AUTH_FIX)
    optional = [s for s in OPTIONAL_SCOPES if not covered(s)]
    if optional:
        return _warn("shopify-store-auth", f"{DEFAULT_STORE}: no {', '.join(optional)} "
                     "(optional: shop policies are not exported without it)", AUTH_FIX)
    return _pass("shopify-store-auth", f"{DEFAULT_STORE}: {', '.join(REQUIRED_SCOPES)}")


def _first_json(text: str) -> Any:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = min((i for i in (text.find("["), text.find("{")) if i >= 0), default=-1)
        if start < 0:
            raise
        return json.JSONDecoder().raw_decode(text, start)[0]


def check_theme_access(env: DoctorEnv) -> Check:
    """The publish starts with `shopify theme pull`, which uses the Shopify account login."""
    login_fix = ("shopify auth login (it opens the browser: sign in with the Shopify account the owner "
                 "invited), then run make doctor again")
    command = [env.which("shopify"), "theme", "list", "--store", DEFAULT_STORE, "--id", DEFAULT_THEME,
               "--no-input", "--no-color", "--json"]
    # A neutral cwd and files for output: never a stdout pipe (Windows mis-decodes it).
    with tempfile.TemporaryDirectory(prefix="rosart-doctor-") as tmp:
        out_path, err_path = Path(tmp) / "stdout.json", Path(tmp) / "stderr.txt"
        try:
            with out_path.open("wb") as out, err_path.open("wb") as err:
                completed = env.shopify_process(command, cwd=tmp, stdout=out, stderr=err,
                                                stdin=subprocess.DEVNULL, timeout=SHOPIFY_TIMEOUT)
        except (OSError, subprocess.SubprocessError) as exc:
            return _fail("shopify-theme-access", f"shopify theme list did not run: {exc}", login_fix)
        raw, console = out_path.read_bytes(), err_path.read_bytes().decode("utf-8", errors="replace")
    if completed.returncode != 0:
        return _fail("shopify-theme-access", f"cannot list the store's themes: {console.strip()[-300:]}",
                     login_fix)
    try:
        themes = _first_json(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _fail("shopify-theme-access", "shopify theme list printed no theme list", login_fix)
    if isinstance(themes, dict):
        themes = themes.get("themes") or [themes]
    if not any(isinstance(t, dict) and str(t.get("id")) == DEFAULT_THEME for t in themes or []):
        return _fail("shopify-theme-access", f"theme {DEFAULT_THEME} (the one the site is built from) "
                     "is not in the store's theme list", "stop and contact the owner")
    return _pass("shopify-theme-access", f"theme {DEFAULT_THEME} on {DEFAULT_STORE}")


# --- the run ------------------------------------------------------------------------------

def run_checks(env: DoctorEnv) -> list[Check]:
    checks = [check_tool(env, tool) for tool in TOOLS]
    passed = {c.name for c in checks if c.status == "PASS"}

    if "git" in passed:
        repo = check_repo(env)
        checks.append(repo)
        if repo.status == "PASS":
            checks += [check_branch(env), check_working_tree(env), check_remote(env), check_git_identity(env)]
        else:
            checks += [_skipped(name, "repo") for name in ("branch", "working-tree", "remote", "git-identity")]
    else:
        checks += [_skipped(name, "git") for name in ("repo", "branch", "working-tree", "remote", "git-identity")]

    checks.append(check_node_modules(env))
    config_check, config = check_deploy_config(env)
    checks.append(config_check)
    if config is None:
        checks.append(_skipped("aws-profile", "deploy-config"))
    elif "aws-cli" not in passed:
        checks.append(_skipped("aws-profile", "aws-cli"))
    else:
        checks.append(check_aws_profile(env, config))

    if "shopify-cli" in passed:
        checks += [check_store_auth(env), check_theme_access(env)]
    else:
        checks += [_skipped(name, "shopify-cli") for name in ("shopify-store-auth", "shopify-theme-access")]
    return checks


def format_report(checks: list[Check]) -> str:
    lines: list[str] = []
    for check in checks:
        lines.append(f"{check.status} {check.name}: {check.detail}")
        if check.status != "PASS" and check.fix:
            lines.append(f"  fix: {check.fix}")
    failed = sum(c.status == "FAIL" for c in checks)
    warned = sum(c.status == "WARN" for c in checks)
    lines.append("")
    if failed:
        lines.append(f"NOT READY: {failed} FAIL, {warned} WARN. Fix the FAIL lines in order, "
                     "then run make doctor again.")
    else:
        lines.append(f"READY to publish ({warned} WARN).")
    return "\n".join(lines)


def main(argv: Optional[list[str]] = None, env: Optional[DoctorEnv] = None) -> int:
    checks = run_checks(env or real_env())
    print(format_report(checks))
    return 1 if any(c.status == "FAIL" for c in checks) else 0


# --- the real system ----------------------------------------------------------------------

# Removed from every child's environment, so the AWS CLI uses only the named profile.
STRIPPED_ENV = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN",
                "AWS_PROFILE", "AWS_DEFAULT_PROFILE")


def _real_run(command: list[str], timeout: int = 60) -> CommandResult:
    executable = shutil.which(command[0])
    if not executable:
        return CommandResult(127, "", f"{command[0]} not found on PATH")
    env = {k: v for k, v in os.environ.items() if k.upper() not in STRIPPED_ENV}
    env.update({"AWS_PAGER": "", "GIT_TERMINAL_PROMPT": "0"})
    try:
        completed = subprocess.run([executable, *command[1:]], cwd=REPO_ROOT, capture_output=True,
                                   text=True, encoding="utf-8", errors="replace", env=env,
                                   stdin=subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired:
        return CommandResult(124, "", f"timed out after {timeout}s")
    return CommandResult(completed.returncode, completed.stdout or "", completed.stderr or "")


def real_env() -> DoctorEnv:
    return DoctorEnv(repo_root=REPO_ROOT, cwd=Path.cwd(), run=_real_run, which=shutil.which,
                     shopify_process=subprocess.run)


if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace", line_buffering=True)
    sys.exit(main(sys.argv[1:]))
