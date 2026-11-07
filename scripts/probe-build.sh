#!/bin/sh
# V-0 CPU probe (sgs-server spec 006, plan Phase 0, quickstart "Box install" step 0): the default command of
# the probe image (`make image-probe`). Copies the read-only /app to the tmpfs workspace /tmp/work, runs the
# site build there (Astro and sharp over the committed export/ images) and prints its duration and peak
# resident memory, so the owner learns whether the box CPU (i7-3770K, no AVX2) can build the site.
# Exits with the build's exit code.
#
# On the box: docker run --rm --network none --read-only --tmpfs /tmp:size=2g --user 10001:10001 <probe image>
set -eu

src="${PROBE_SRC:-/app}"
work="${PROBE_WORK:-/tmp/work}"

# Read-only root and a uid with no home: home, npm cache and Astro scratch all live beside the workspace.
home="$(dirname "$work")/home"
export HOME="$home"
export ASTRO_TELEMETRY_DISABLED=1
export npm_config_cache="$home/.npm"
export npm_config_update_notifier=false

cpu="$(grep -m1 'model name' /proc/cpuinfo 2>/dev/null | cut -d: -f2- | sed 's/^ *//' || true)"
if grep -qw avx2 /proc/cpuinfo 2>/dev/null; then avx2=yes; else avx2=no; fi
echo "probe: arch=$(uname -m) cpu=${cpu:-unknown} avx2=$avx2 node=$(node --version 2>/dev/null || echo missing)"

rm -rf "$work"
mkdir -p "$work" "$home"
cp -R "$src/." "$work/"
cd "$work"

# Python measures the build alone (the copy above is not counted). On Linux ru_maxrss of the children is
# the peak resident memory of the largest single process of the build (the node process), in KiB.
exec python3 - <<'EOF'
import resource
import subprocess
import sys
import time

start = time.monotonic()
code = subprocess.call(["npm", "run", "build"])
duration = time.monotonic() - start
peak_mib = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss // 1024
print(f"probe: npm run build exit={code} duration_s={duration:.1f} peak_rss_mib={peak_mib}", flush=True)
sys.exit(code if code >= 0 else 128 - code)
EOF
