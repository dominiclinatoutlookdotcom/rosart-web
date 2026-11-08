"""Tests for the publisher image's build and probe stages (sgs-server spec 006, T004/T005; research R-12,
contracts/rosart-web-interfaces.md R-5).

The V-0 CPU probe image is the image's build stage (frozen Harbor bases by digest, `npm ci` from the
lockfile, the committed `export/`) with a default command that copies `/app` to `/tmp/work`, runs
`npm run build` and prints the duration and peak memory. The owner runs it once on the box
(`--network none`, read-only root) before any service code is written.

No Docker here: the Dockerfile, .dockerignore and Makefile are read as text, `make image-probe` runs
against a fake `docker`, and the probe script runs against a fake `npm` (POSIX only).
"""
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = ROOT / "Dockerfile"
DOCKERIGNORE = ROOT / ".dockerignore"
MAKEFILE = ROOT / "Makefile"
PROBE_SCRIPT = ROOT / "scripts" / "probe-build.sh"

HARBOR = "harbor.int.solagratia.com.au"
FROZEN = f"{HARBOR}/frozen/"
PROBE_IMAGE = f"{HARBOR}/demo/rosart-publisher-probe"
# Until owner task T001 freezes the bases into Harbor, the digest ARGs hold this placeholder, which is
# not a valid digest: `docker build` and `make image-probe` refuse it.
DIGEST_PLACEHOLDER = "PENDING-T001"
DIGEST_ARGS = ("NODE_SHA256", "PYTHON_SHA256")
FAKE_DIGEST = "sha256:" + "ab" * 32


# --- Dockerfile reading ---------------------------------------------------------------------------

def instructions(text: str) -> list[tuple[str, str]]:
    """(INSTRUCTION, arguments) pairs, comments dropped and backslash continuations joined."""
    result: list[tuple[str, str]] = []
    pending = ""
    for raw in text.splitlines():
        line = raw.strip()
        if not pending and (not line or line.startswith("#")):
            continue
        if pending and line.startswith("#"):
            continue
        if line.endswith("\\"):
            pending += line[:-1] + " "
            continue
        full = (pending + line).strip()
        pending = ""
        if full:
            keyword, _, rest = full.partition(" ")
            result.append((keyword.upper(), rest.strip()))
    return result


def arg_defaults(text: str) -> dict[str, str]:
    defaults: dict[str, str] = {}
    for keyword, rest in instructions(text):
        if keyword == "ARG" and "=" in rest:
            name, _, value = rest.partition("=")
            defaults[name.strip()] = value.strip().strip('"')
    return defaults


def stages(text: str) -> dict[str, dict]:
    """Stage name -> {"from": raw image reference, "body": [(INSTRUCTION, arguments), ...]}."""
    found: dict[str, dict] = {}
    current = None
    for keyword, rest in instructions(text):
        if keyword == "FROM":
            parts = [p for p in rest.split() if not p.startswith("--")]
            name = parts[2] if len(parts) >= 3 and parts[1].upper() == "AS" else f"stage{len(found)}"
            current = {"from": parts[0], "body": []}
            found[name] = current
        elif current is not None:
            current["body"].append((keyword, rest))
    return found


def resolve(reference: str, defaults: dict[str, str]) -> str:
    return re.sub(r"\$\{(\w+)\}|\$(\w+)", lambda m: defaults.get(m.group(1) or m.group(2), ""), reference)


def copy_sources(body: list[tuple[str, str]]) -> list[str]:
    """Build-context sources of every COPY/ADD in a stage (not `COPY --from=...`)."""
    sources: list[str] = []
    for keyword, rest in body:
        if keyword not in ("COPY", "ADD"):
            continue
        if "--from=" in rest:
            continue
        if rest.startswith("["):
            paths = json.loads(rest)
        else:
            paths = [p for p in rest.split() if not p.startswith("--")]
        sources.extend(paths[:-1])
    return sources


@pytest.fixture(scope="module")
def dockerfile() -> str:
    assert DOCKERFILE.is_file(), "Dockerfile is missing"
    return DOCKERFILE.read_text(encoding="utf-8")


# --- Bases: frozen Harbor images, by digest --------------------------------------------------------

def test_every_external_base_is_a_frozen_harbor_image_pinned_by_digest(dockerfile):
    found = stages(dockerfile)
    defaults = arg_defaults(dockerfile)
    external = {name: s["from"] for name, s in found.items() if s["from"] not in found}
    assert external, "no FROM lines"
    for name, reference in external.items():
        assert reference.startswith(FROZEN), f"stage {name} is not built from Harbor frozen/: {reference}"
        assert re.search(r"@sha256:\$\{\w+\}$", reference), f"stage {name} is not pinned by a digest ARG: {reference}"
        assert resolve(reference, defaults).startswith(FROZEN)


def test_the_build_stage_is_python_with_node_copied_from_the_frozen_node_image(dockerfile):
    found = stages(dockerfile)
    assert "build" in found, "no stage named build"
    assert found["build"]["from"].startswith(f"{FROZEN}python@sha256:")
    node_stages = [n for n, s in found.items() if s["from"].startswith(f"{FROZEN}node@sha256:")]
    assert node_stages, "no stage from frozen/node"
    copied_from = {re.search(r"--from=(\S+)", rest).group(1)
                   for keyword, rest in found["build"]["body"] if keyword == "COPY" and "--from=" in rest}
    assert copied_from & set(node_stages), "the build stage does not copy Node from the frozen/node stage"


def test_base_digest_args_hold_a_real_digest_or_the_t001_placeholder(dockerfile):
    defaults = arg_defaults(dockerfile)
    for name in DIGEST_ARGS:
        assert name in defaults, f"ARG {name} has no default"
        value = defaults[name]
        assert re.fullmatch(r"[0-9a-f]{64}", value) or value == DIGEST_PLACEHOLDER, \
            f"ARG {name}={value!r} is neither a sha256 digest nor {DIGEST_PLACEHOLDER}"
    references = " ".join(s["from"] for s in stages(dockerfile).values())
    for name in DIGEST_ARGS:
        assert f"@sha256:${{{name}}}" in references, f"no FROM uses ${{{name}}}"


# --- Build inputs --------------------------------------------------------------------------------

def test_dependencies_come_from_the_lockfile_with_npm_ci_never_npm_install(dockerfile):
    build = stages(dockerfile)["build"]["body"]
    runs = [rest for keyword, rest in build if keyword == "RUN"]
    assert any(re.search(r"\bnpm ci\b", run) for run in runs), "the build stage never runs npm ci"
    assert "package-lock.json" in copy_sources(build)
    assert not re.search(r"\bnpm (install|i|add)\b", dockerfile), "npm install found: use npm ci"


def test_the_committed_export_is_copied_but_never_export_theme(dockerfile):
    sources = [s.rstrip("/") for name, s in stages(dockerfile).items() for s in copy_sources(s["body"])]
    assert "." not in sources and "export" not in sources, "copy the export subfolders, not the whole folder"
    assert not any(s.startswith("export/theme") and s != "export/theme-content.json" for s in sources)
    for needed in ("export/shopify", "export/images", "export/theme-content.json"):
        assert needed in sources, f"{needed} is not copied into the image"


def test_dockerignore_keeps_secrets_build_output_and_the_theme_out_of_the_context():
    assert DOCKERIGNORE.is_file(), ".dockerignore is missing"
    lines = {line.strip() for line in DOCKERIGNORE.read_text(encoding="utf-8").splitlines()}
    for pattern in (".env*", "node_modules", "dist", ".astro", "export/theme"):
        assert pattern in lines, f".dockerignore does not list {pattern}"


def test_no_arg_or_env_is_named_after_a_secret(dockerfile):
    secretish = re.compile(r"TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|ACCESS_KEY|API_KEY|PRIVATE", re.I)
    for keyword, rest in instructions(dockerfile):
        if keyword in ("ARG", "ENV"):
            name = re.split(r"[=\s]", rest, maxsplit=1)[0]
            assert not secretish.search(name), f"{keyword} {name} looks like a secret"


# --- Probe stage and script ----------------------------------------------------------------------

def test_the_probe_stage_extends_build_and_runs_the_probe_script_by_default(dockerfile):
    found = stages(dockerfile)
    assert "probe" in found, "no stage named probe"
    assert found["probe"]["from"] == "build"
    cmds = [rest for keyword, rest in found["probe"]["body"] if keyword == "CMD"]
    assert cmds, "the probe stage has no CMD"
    assert "scripts/probe-build.sh" in cmds[-1]
    assert "scripts/probe-build.sh" in copy_sources(found["probe"]["body"]) + copy_sources(found["build"]["body"])


def test_the_probe_script_is_a_posix_script_with_lf_line_endings():
    assert PROBE_SCRIPT.is_file(), "scripts/probe-build.sh is missing"
    data = PROBE_SCRIPT.read_bytes()
    assert data.startswith(b"#!/bin/sh"), "the probe script must start with #!/bin/sh"
    assert b"\r" not in data, "CRLF in the probe script: sh in the image would fail"
    text = data.decode("utf-8")
    assert "/app" in text and "/tmp/work" in text
    assert "npm run build" in text
    assert "ASTRO_TELEMETRY_DISABLED=1" in text


@pytest.mark.skipif(sys.platform == "win32", reason="the probe script runs in the Linux image (POSIX only)")
def test_the_probe_script_copies_the_app_builds_it_offline_and_reports_time_and_memory(tmp_path):
    app = tmp_path / "app"
    (app / "export").mkdir(parents=True)
    (app / "export" / "marker.txt").write_text("committed export", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "npm-call.txt"
    fake_npm = bin_dir / "npm"
    fake_npm.write_text(
        "#!/bin/sh\n"
        f'printf "%s|%s|%s|%s\\n" "$*" "$(pwd)" "$ASTRO_TELEMETRY_DISABLED" "$(cat export/marker.txt)" > "{record}"\n'
        "exit ${FAKE_NPM_EXIT:-0}\n",
        encoding="utf-8",
    )
    fake_npm.chmod(0o755)
    work = tmp_path / "work"
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
           "PROBE_SRC": str(app), "PROBE_WORK": str(work), "HOME": str(tmp_path / "home")}
    env.pop("ASTRO_TELEMETRY_DISABLED", None)

    completed = subprocess.run(["sh", str(PROBE_SCRIPT)], env=env, capture_output=True, text=True)

    assert completed.returncode == 0, completed.stderr
    args, cwd, telemetry, marker = record.read_text(encoding="utf-8").strip().split("|")
    assert args == "run build"
    assert Path(cwd).resolve() == work.resolve()
    assert telemetry == "1"
    assert marker == "committed export"
    assert re.search(r"duration_s=\d+(\.\d+)?", completed.stdout), completed.stdout
    assert re.search(r"peak_rss_mib=\d+", completed.stdout), completed.stdout

    env["FAKE_NPM_EXIT"] = "3"
    failed = subprocess.run(["sh", str(PROBE_SCRIPT)], env=env, capture_output=True, text=True)
    assert failed.returncode == 3, "the probe must exit with the build's exit code"


# --- make image-probe, against a fake docker -----------------------------------------------------

needs_make = pytest.mark.skipif(shutil.which("make") is None or shutil.which("sh") is None,
                                reason="make and sh are needed to run the Makefile")


def run_image_probe(tmp_path: Path, dockerfile_text: str) -> tuple[subprocess.CompletedProcess, str]:
    shutil.copy(MAKEFILE, tmp_path / "Makefile")
    (tmp_path / "Dockerfile").write_text(dockerfile_text, encoding="utf-8", newline="\n")
    log = tmp_path / "docker-calls.txt"
    fake_docker = tmp_path / "fake-docker.sh"
    fake_docker.write_text(
        f'printf "%s\\n" "$*" >> "{log.as_posix()}"\n'
        'case "$*" in\n'
        f'  *imagetools*) printf "Name: x\\nMediaType: application/vnd.oci.image.index.v1+json\\nDigest: {FAKE_DIGEST}\\n" ;;\n'
        "esac\n",
        encoding="utf-8",
        newline="\n",
    )
    completed = subprocess.run(
        ["make", "-s", "image-probe", f"DOCKER=sh {fake_docker.as_posix()}", "PROBE_TAG=abc1234"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    calls = log.read_text(encoding="utf-8") if log.exists() else ""
    return completed, calls


def with_digests(text: str, value: str) -> str:
    for name in DIGEST_ARGS:
        text = re.sub(rf"^ARG {name}=.*$", f"ARG {name}={value}", text, flags=re.M)
    return text


@needs_make
def test_image_probe_refuses_while_a_base_digest_is_the_placeholder(tmp_path, dockerfile):
    completed, calls = run_image_probe(tmp_path, with_digests(dockerfile, DIGEST_PLACEHOLDER))
    assert completed.returncode != 0
    assert "T001" in completed.stdout + completed.stderr
    assert calls == "", "docker was called although the bases are not pinned"


@needs_make
def test_image_probe_builds_the_probe_target_for_amd64_pushes_it_and_prints_the_digest(tmp_path, dockerfile):
    completed, calls = run_image_probe(tmp_path, with_digests(dockerfile, "cd" * 32))
    assert completed.returncode == 0, completed.stdout + completed.stderr
    build = next(line for line in calls.splitlines() if "build" in line.split()[:2])
    assert "--platform linux/amd64" in build
    assert "--target probe" in build
    assert f"{PROBE_IMAGE}:abc1234" in build
    assert "--push" in build
    assert "imagetools inspect" in calls
    assert FAKE_DIGEST in completed.stdout
    assert f"{PROBE_IMAGE}@{FAKE_DIGEST}" in completed.stdout, "print the V-0 reference by digest"


# --- Lockfile completeness ------------------------------------------------------------------------

def _resolve_in_lock(packages: dict, holder: str, name: str):
    """The lock entry Node would load for `name` from the package at `holder` (walk up node_modules)."""
    base = holder
    while True:
        entry = packages.get(f"{base}/node_modules/{name}" if base else f"node_modules/{name}")
        if entry is not None or not base:
            return entry
        base = base.rpartition("/node_modules/")[0] if "/node_modules/" in base else ""


def test_lockfile_resolves_every_exactly_pinned_dependency_for_a_linux_npm_ci():
    # Why: a lock written on Windows by an older npm omitted the Linux-only wasm32 entries (nested
    # @emnapi/core and runtime 2.0.0-beta.2 under @rolldown/binding-wasm32-wasi), and `npm ci` in the
    # image failed with "Missing ... from lock file". npm ci cannot run here (no network, wrong
    # platform), so check the cause directly: each dependency declared with an exact version must
    # resolve, by Node's lookup, to a lock entry of that version, optional platform packages included.
    packages = json.loads((ROOT / "package-lock.json").read_text(encoding="utf-8"))["packages"]
    exact = re.compile(r"^\d+\.\d+\.\d+(-[0-9A-Za-z.-]+)?$")
    missing = []
    for path, meta in packages.items():
        for name, wanted in meta.get("dependencies", {}).items():
            if not exact.match(wanted):
                continue
            found = _resolve_in_lock(packages, path, name)
            if found is None or found.get("version") != wanted:
                missing.append(f"{path or '(root)'} needs {name}@{wanted}")
    assert not missing, "lockfile is incomplete:\n" + "\n".join(missing)


# =================================================================================================
# Final and test stages, `make image`, `make image-test` (sgs-server spec 006, T097/T098; R-5, R-12)
# =================================================================================================

IMAGE = f"{HARBOR}/demo/rosart-publisher"
AWSCLI_ARGS = ("AWSCLI_VERSION", "AWSCLI_SHA256")


def chain(found: dict[str, dict], name: str) -> list[tuple[str, str]]:
    """Every instruction a stage inherits, base stages first (FROM <stage> chains)."""
    stage = found[name]
    inherited = chain(found, stage["from"]) if stage["from"] in found else []
    return inherited + stage["body"]


def env_values(body: list[tuple[str, str]]) -> dict[str, str]:
    values: dict[str, str] = {}
    for keyword, rest in body:
        if keyword != "ENV":
            continue
        if "=" in rest.split()[0]:
            for pair in shlex.split(rest):
                name, _, value = pair.partition("=")
                values[name] = value
        else:
            name, _, value = rest.partition(" ")
            values[name] = value.strip().strip('"')
    return values


def final_chain(dockerfile: str) -> list[tuple[str, str]]:
    found = stages(dockerfile)
    assert "final" in found, "no stage named final"
    return chain(found, "final")


def test_the_final_stage_builds_on_the_frozen_python_base_through_the_build_stage(dockerfile):
    found = stages(dockerfile)
    assert "final" in found, "no stage named final"
    assert found["final"]["from"] == "build"
    assert found["build"]["from"].startswith(f"{FROZEN}python@sha256:")
    runs = " ".join(rest for keyword, rest in final_chain(dockerfile) if keyword == "RUN")
    assert re.search(r"node --version.*v24\.", runs), "the image does not assert Node 24"
    assert "3.14" in runs and "python --version" in runs, "the image does not assert Python 3.14"
    apt = " ".join(rest for keyword, rest in final_chain(dockerfile) if keyword == "RUN" and "apt-get install" in rest)
    assert re.search(r"\bgit\b", apt) and "tzdata" in apt


def test_the_aws_cli_comes_from_a_pinned_zip_verified_by_its_sha256(dockerfile):
    defaults = arg_defaults(dockerfile)
    for name in AWSCLI_ARGS:
        assert name in defaults, f"ARG {name} has no default"
    assert re.fullmatch(r"\d+\.\d+\.\d+", defaults["AWSCLI_VERSION"])
    assert re.fullmatch(r"[0-9a-f]{64}", defaults["AWSCLI_SHA256"]) or defaults["AWSCLI_SHA256"] == DIGEST_PLACEHOLDER
    adds = [rest for keyword, rest in instructions(dockerfile) if keyword == "ADD"]
    assert len(adds) == 1, "exactly one download: the AWS CLI zip"
    assert "--checksum=sha256:${AWSCLI_SHA256}" in adds[0]
    assert "https://awscli.amazonaws.com/awscli-exe-linux-x86_64-${AWSCLI_VERSION}.zip" in adds[0]
    assert not re.search(r"\b(curl|wget)\b", dockerfile), "downloads go through ADD --checksum only"
    body = final_chain(dockerfile)
    assert any(keyword == "COPY" and "--from=awscli" in rest for keyword, rest in body), \
        "the final stage does not copy the installed AWS CLI from the awscli stage"
    assert any(keyword == "RUN" and "aws --version" in rest for keyword, rest in body)


def test_the_image_runs_as_uid_10001_and_app_stays_root_owned_and_world_readable(dockerfile):
    body = final_chain(dockerfile)
    users = [rest for keyword, rest in body if keyword == "USER"]
    assert users and users[-1] == "10001:10001", f"the final stage must end as 10001:10001, got {users}"
    assert ("WORKDIR", "/app") in body
    for keyword, rest in body:
        if keyword in ("COPY", "ADD"):
            assert "--chown" not in rest, f"{keyword} {rest}: /app must stay root-owned"
        if keyword == "RUN":
            assert not re.search(r"\bchown\b.*/app", rest), f"RUN {rest}: /app must stay root-owned"
            assert not re.search(r"\bchmod\b.*(777|o\+w|a\+w|go\+w)", rest), f"RUN {rest}: /app must not be writable"
    copied = [s for keyword, rest in body if keyword == "COPY" and "--from=" not in rest
              for s in copy_sources([(keyword, rest)])]
    for needed in ("publisher", "tools", "scripts", "deploy.config.json"):
        assert needed in copied, f"{needed} is not copied into the final image"
    assert "tests" not in copied, "tests belong to the test stage only"


def test_the_image_sets_telemetry_off_and_the_tool_and_home_variables(dockerfile):
    env = env_values(final_chain(dockerfile))
    expected = {
        "ASTRO_TELEMETRY_DISABLED": "1", "NPM_CONFIG_UPDATE_NOTIFIER": "false", "NPM_CONFIG_FUND": "false",
        "NPM_CONFIG_AUDIT": "false", "NPM_CONFIG_CACHE": "/tmp/npm", "HOME": "/tmp/home", "AWS_PAGER": "",
        "AWS_CLI_AUTO_PROMPT": "off", "PYTHONDONTWRITEBYTECODE": "1",
    }
    for name, value in expected.items():
        assert env.get(name) == value, f"ENV {name} should be {value!r}, found {env.get(name)!r}"
    assert not [n for n in env if n.upper().endswith("_PROXY")], "no proxy variable"


def test_the_code_commit_is_a_build_argument_the_service_reads(dockerfile):
    body = final_chain(dockerfile)
    assert any(keyword == "ARG" and rest.split("=")[0] == "PUBLISHER_CODE_COMMIT" for keyword, rest in body)
    assert env_values(body).get("PUBLISHER_CODE_COMMIT") == "${PUBLISHER_CODE_COMMIT}"


def test_the_healthcheck_asks_the_service_and_the_default_command_serves(dockerfile):
    body = stages(dockerfile)["final"]["body"]
    health = [rest for keyword, rest in body if keyword == "HEALTHCHECK"]
    assert health, "the final stage has no HEALTHCHECK"
    assert json.loads(health[-1][health[-1].index("CMD") + 3:].strip()) == ["python", "-m", "publisher", "healthcheck"]
    cmds = [rest for keyword, rest in body if keyword == "CMD"]
    assert cmds and json.loads(cmds[-1]) == ["python", "-m", "publisher", "serve"]


def test_no_arg_or_env_in_the_final_chain_names_a_secret(dockerfile):
    names = [re.split(r"[=\s]", rest, maxsplit=1)[0]
             for keyword, rest in final_chain(dockerfile) if keyword in ("ARG", "ENV")]
    assert "PUBLISHER_CODE_COMMIT" in names and "HOME" in names
    for name in names:
        assert not re.search(r"TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|ACCESS_KEY|API_KEY|PRIVATE", name, re.I), name


def test_the_test_stage_runs_the_whole_suite_with_posix_tests_required(dockerfile):
    found = stages(dockerfile)
    assert "test" in found, "no stage named test"
    assert found["test"]["from"] == "final"
    body = found["test"]["body"]
    assert env_values(body).get("PUBLISHER_REQUIRE_POSIX") == "1"
    cmds = [rest for keyword, rest in body if keyword == "CMD"]
    assert cmds, "the test stage has no CMD"
    argv = json.loads(cmds[-1])
    assert argv[:4] == ["python", "-m", "pytest", "-q"] and argv[-1] == "tests"
    assert "tests" in copy_sources(body)
    runs = " ".join(rest for keyword, rest in body if keyword == "RUN")
    assert re.search(r"pytest==\d+\.\d+\.\d+", runs), "pytest must be pinned"
    users = [rest for keyword, rest in body if keyword == "USER"]
    assert users and users[-1] == "10001:10001", "the suite runs as the service's own user"
    others = [s for name, st in found.items() if name != "test" for s in copy_sources(st["body"])]
    assert "tests" not in others


def test_the_probe_command_and_the_docs_use_an_exec_tmpfs():
    # Why: Docker mounts --tmpfs noexec by default and the V-0 probe ended with exit 126 (research
    # "Verified on the box"). Never print or document a tmpfs for the build workspace without exec.
    bare = re.compile(r"--tmpfs /tmp:size=2g(?!,exec)")
    for path in (MAKEFILE, DOCKERFILE, ROOT / "README.md"):
        text = path.read_text(encoding="utf-8")
        assert "--tmpfs /tmp:size=2g,exec" in text, f"{path.name} does not show --tmpfs /tmp:size=2g,exec"
        assert not bare.search(text), f"{path.name} still shows a noexec --tmpfs /tmp:size=2g"


# --- make image / make image-test, against a fake docker ------------------------------------------

needs_git_make = pytest.mark.skipif(
    shutil.which("make") is None or shutil.which("sh") is None or shutil.which("git") is None,
    reason="make, sh and git are needed to run the Makefile")


def git(repo: Path, *args: str) -> str:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}
    return subprocess.run(["git", *args], cwd=repo, env=env, capture_output=True, text=True, check=True).stdout.strip()


def make_repo(root: Path, dockerfile_text: str) -> Path:
    repo = root / "repo"
    repo.mkdir(parents=True)
    shutil.copy(MAKEFILE, repo / "Makefile")
    (repo / "Dockerfile").write_text(dockerfile_text, encoding="utf-8", newline="\n")
    git(repo, "init", "-q")
    git(repo, "add", "Makefile", "Dockerfile")
    git(repo, "commit", "-q", "-m", "init")
    return repo


def run_make(root: Path, repo: Path, *make_args: str, docker_fail: str = "") -> tuple[subprocess.CompletedProcess, str]:
    fake_dir = root / "fake"
    fake_dir.mkdir(exist_ok=True)
    log = fake_dir / "docker-calls.txt"
    fake_docker = fake_dir / "fake-docker.sh"
    fail = f'case "$*" in *"{docker_fail}"*) exit 7 ;; esac\n' if docker_fail else ""
    fake_docker.write_text(
        f'printf "%s\\n" "$*" >> "{log.as_posix()}"\n'
        f'case "$*" in\n'
        f'  *imagetools*) printf "Name: x\\nDigest: {FAKE_DIGEST}\\n" ;;\n'
        f'esac\n' + fail,
        encoding="utf-8", newline="\n")
    completed = subprocess.run(["make", "-s", *make_args, f"DOCKER=sh {fake_docker.as_posix()}"],
                               cwd=repo, capture_output=True, text=True)
    return completed, log.read_text(encoding="utf-8") if log.exists() else ""


def pinned(text: str) -> str:
    text = with_digests(text, "cd" * 32)
    return re.sub(r"^ARG AWSCLI_SHA256=.*$", "ARG AWSCLI_SHA256=" + "ef" * 32, text, flags=re.M)


@needs_git_make
def test_image_refuses_without_a_version_or_with_a_malformed_one(tmp_path, dockerfile):
    repo = make_repo(tmp_path, pinned(dockerfile))
    for args in ((), ("VERSION=1.0.0",), ("VERSION=v1.0",), ("VERSION=latest",)):
        completed, calls = run_make(tmp_path, repo, "image", *args)
        assert completed.returncode != 0, args
        assert "VERSION" in completed.stdout + completed.stderr
        assert calls == "", "docker was called without a valid VERSION"


@needs_git_make
def test_image_refuses_a_dirty_tree_including_untracked_files(tmp_path, dockerfile):
    repo = make_repo(tmp_path, pinned(dockerfile))
    (repo / "scratch.txt").write_text("not committed", encoding="utf-8")
    completed, calls = run_make(tmp_path, repo, "image", "VERSION=v1.2.3")
    assert completed.returncode != 0
    assert "clean" in (completed.stdout + completed.stderr).lower()
    assert calls == "", "docker was called on a dirty tree"


@needs_git_make
@pytest.mark.parametrize("name", [*DIGEST_ARGS, "AWSCLI_SHA256"])
def test_image_refuses_while_a_pinned_digest_is_still_the_placeholder(tmp_path, dockerfile, name):
    text = re.sub(rf"^ARG {name}=.*$", f"ARG {name}={DIGEST_PLACEHOLDER}", pinned(dockerfile), flags=re.M)
    repo = make_repo(tmp_path, text)
    completed, calls = run_make(tmp_path, repo, "image", "VERSION=v1.2.3")
    assert completed.returncode != 0
    assert name in completed.stdout + completed.stderr
    assert calls == "", f"docker was called while {name} is not pinned"


@needs_git_make
def test_image_builds_the_final_target_for_amd64_pushes_the_version_tag_and_prints_the_digest(tmp_path, dockerfile):
    repo = make_repo(tmp_path, pinned(dockerfile))
    head = git(repo, "rev-parse", "HEAD")
    completed, calls = run_make(tmp_path, repo, "image", "VERSION=v1.2.3")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    build = next(line for line in calls.splitlines() if "build" in line.split()[:2])
    assert "--platform linux/amd64" in build
    assert "--target final" in build
    assert f"-t {IMAGE}:v1.2.3" in build
    assert "--push" in build
    assert f"--build-arg PUBLISHER_CODE_COMMIT={head}" in build
    assert "imagetools inspect" in calls and f"{IMAGE}:v1.2.3" in calls
    assert FAKE_DIGEST in completed.stdout
    assert f"{IMAGE}:v1.2.3@{FAKE_DIGEST}" in completed.stdout, "print the compose image line"
    assert head in completed.stdout, "print the rosart-web commit for the digest-bump commit"


@needs_git_make
def test_image_test_builds_and_runs_the_test_stage_then_the_selftest_in_the_final_image(tmp_path, dockerfile):
    repo = make_repo(tmp_path, pinned(dockerfile))
    completed, calls = run_make(tmp_path, repo, "image-test")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    lines = calls.splitlines()
    builds = [line for line in lines if "build" in line.split()[:2]]
    assert any("--target test" in line for line in builds), "the test stage is not built"
    assert any("--target final" in line for line in builds), "the final stage is not built for the selftest"
    assert all("--push" not in line for line in lines), "image-test never pushes"
    runs = [line for line in lines if line.split()[:1] == ["run"]]
    assert len(runs) == 2, runs
    suite = next(line for line in runs if "selftest" not in line)
    assert "--rm" in suite and "--tmpfs /tmp:size=2g,exec" in suite
    assert "--init" in suite.split(), "the suite runs with an init, as production does (zombie reaping)"
    selftest = next(line for line in runs if "selftest" in line)
    assert "--init" in selftest.split(), "the selftest runs with an init, as production does"
    assert "--read-only" in selftest and "--user 10001:10001" in selftest and "python -m publisher selftest" in selftest
    assert lines.index(suite) < lines.index(selftest)


@needs_git_make
def test_image_test_fails_when_the_suite_fails_and_skips_the_selftest(tmp_path, dockerfile):
    repo = make_repo(tmp_path, pinned(dockerfile))
    completed, calls = run_make(tmp_path, repo, "image-test", docker_fail="run --rm")
    assert completed.returncode != 0
    assert "selftest" not in calls
