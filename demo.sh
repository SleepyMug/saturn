#!/usr/bin/env bash
# demo.sh — saturn capability showcase.
#
# Three acts:
#   1. Engine detection      — adaptive BuildKit + podman/docker mismatch fail.
#   2. Bind-mount nesting    — one compose.yaml, works host and guest.
#   3. Dev vs handoff        — host-owned files + vanilla-compose compat.
#
# Prereqs: rootless podman running (systemctl --user enable --now podman.socket),
# docker-cli + compose plugin on $PATH. If the saturn base image is missing the
# demo will build it once (~1 min).
#
# Runs end-to-end in ~60 seconds after base image is present. Re-runnable;
# trap-cleans its workspaces and containers on exit.

set -u

DEMO_ROOT="${DEMO_ROOT:-/tmp/saturn-demo}"
# Prefer the saturn next to this script (the repo's working copy) so the
# demo always showcases the version we just built, not a stale one from
# /usr/local/bin. Override with SATURN_BIN=/path/to/saturn if needed.
SATURN_BIN="${SATURN_BIN:-$(dirname "$(readlink -f "$0")")/saturn}"
[[ -x "$SATURN_BIN" ]] || SATURN_BIN="$(command -v saturn || true)"
BASE_IMAGE="${SATURN_BASE_IMAGE:-localhost/saturn-base:latest}"

# Point every bare `docker` command in this script at the same socket
# saturn will auto-select — otherwise `docker ps` / `docker inspect` may
# hit rootful docker on hosts that also have dockerd installed, missing
# the saturn containers (which live on rootless podman by default).
if [[ -z "${DOCKER_HOST:-}" ]]; then
  _xdg="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
  for _c in "$_xdg/podman/podman.sock" "$_xdg/docker.sock" "/var/run/docker.sock"; do
    if [[ -S "$_c" ]]; then export SATURN_SOCK="$_c"; break; fi
  done
  export DOCKER_HOST="unix://${SATURN_SOCK:-$_c}"
fi

blue()   { printf '\033[1;34m%s\033[0m' "$*"; }
dim()    { printf '\033[90m%s\033[0m' "$*"; }
cyan()   { printf '\033[36m%s\033[0m' "$*"; }
banner() { printf '\n%s\n'   "$(blue "━━━ $* ━━━")"; }
note()   { printf '%s\n'     "$(dim  "# $*")"; }
show()   { printf '%s %s\n'  "$(cyan '$')" "$*"; }

cleanup() {
  cd /
  docker rm -f saturn_demoouter saturn_demosub >/dev/null 2>&1 || true
  rm -rf "$DEMO_ROOT"
}
trap cleanup EXIT

# ───── prereqs ────────────────────────────────────────────────────────

[[ -x "$SATURN_BIN" ]] || { echo "saturn script not on \$PATH and not next to demo.sh"; exit 1; }
command -v docker >/dev/null || { echo "docker CLI required"; exit 1; }

if ! docker image inspect "$BASE_IMAGE" >/dev/null 2>&1; then
  banner "Prep — building $BASE_IMAGE (one-time, ~1 min)"
  "$SATURN_BIN" base default
fi

cleanup                 # clear prior demo leftovers
mkdir -p "$DEMO_ROOT"

# ───── ACT 1 — engine detection ──────────────────────────────────────

banner "ACT 1 — Engine detection"
note "Saturn probes at import:"
note "  _detect_cli()     : parses 'docker --version' (catches podman shims)"
note "  _detect_backend() : 'docker version' stdout — 'Podman Engine' ⇒ podman"
note "From those two, saturn decides DOCKER_BUILDKIT and whether to fail fast."

probe_env() {
  python3 - <<PY
import os, runpy
ns = runpy.run_path("$SATURN_BIN", run_name="probe")
cli       = ns.get("_cli",        "skipped")
backend   = ns.get("_backend",    "skipped")
root_owned= ns.get("_root_owned", "skipped")
buildkit  = os.environ.get("DOCKER_BUILDKIT", "<unset>")
print(f"  → cli={cli}  backend={backend}  root_owned={root_owned}")
print(f"  → DOCKER_BUILDKIT = {buildkit}")
PY
}

banner "1a — docker-cli × rootless podman (default socket)"
note "Rootless podman compat socket doesn't serve BuildKit → classic builder."
show 'saturn --help  (probe runs at import)'
probe_env

banner "1b — docker-cli × rootful docker (/var/run/docker.sock)"
note "Socket is root-owned → saturn prints the load-bearing-rootless warning."
note "Check B's gate blocks BuildKit on rootful regardless of backend."
show 'SATURN_SOCK=/var/run/docker.sock saturn --help'
SATURN_SOCK=/var/run/docker.sock probe_env

banner "1c — podman shim × docker backend → FAIL FAST"
note "Simulate the podman-docker package: /usr/bin/docker execs podman."
note "podman --remote only speaks podman's REST API; dockerd socket → 404."
note "Saturn detects the CLI/backend mismatch and exits before any compose call."
mkdir -p "$DEMO_ROOT/shim"
cat > "$DEMO_ROOT/shim/docker" <<'SHIM'
#!/bin/bash
exec podman --remote --url="unix://${SATURN_SOCK:-/var/run/docker.sock}" "$@"
SHIM
chmod +x "$DEMO_ROOT/shim/docker"
show 'PATH=shim:$PATH SATURN_SOCK=/var/run/docker.sock saturn --help'
PATH="$DEMO_ROOT/shim:$PATH" SATURN_SOCK=/var/run/docker.sock "$SATURN_BIN" --help 2>&1 || note "saturn exited $? — correct (mismatch rejected)"

# ───── ACT 2 — bind-mount nesting ────────────────────────────────────

banner "ACT 2 — Bind-mount nesting"
note "One compose.yaml. Works on host (paths expand host-side) and inside a"
note "saturn container (paths are inside-container; the inner saturn reverse-"
note "looks them up via docker inspect \$self, so the sibling runs on the host."

banner "2a — Create an outer workspace"
show "saturn new $DEMO_ROOT/demoouter --socket"
"$SATURN_BIN" new "$DEMO_ROOT/demoouter" --socket
note "The seeded compose.yaml uses \${HOME} and \${SATURN_SOCK} — same file works"
note "in both modes. Excerpt:"
sed -n '1,25p' "$DEMO_ROOT/demoouter/.saturn/compose.yaml"

banner "2b — Bring the outer up"
show "cd $DEMO_ROOT/demoouter && saturn up -d"
(cd "$DEMO_ROOT/demoouter" && "$SATURN_BIN" up -d)

banner "2c — From inside: create + up a sibling workspace"
note "Nesting demo: the inner saturn sees \$PWD = /root/demoouter. When it"
note "writes a sub-workspace and runs 'saturn up', it translates bind sources"
note "from inside-paths back to real host paths via docker inspect."
show "saturn exec -T dev bash <<'INSIDE'  (nested workspace creation)"
(cd "$DEMO_ROOT/demoouter" && "$SATURN_BIN" exec -T dev bash -lc '
set -eu
cd /root/demoouter
mkdir -p demosub && cd demosub
saturn new --socket
saturn up -d
echo
echo "── host engine view from inside the outer container ──"
docker ps --filter name=saturn_ --format "table {{.Names}}\t{{.Image}}"
')

banner "2d — Back on host: both containers live on the host engine"
show 'docker ps --filter name=saturn_'
docker ps --filter name=saturn_ --format 'table {{.Names}}\t{{.Image}}'
note "The inner saturn did not create a podman-inside-podman. saturn_demosub"
note "runs as a sibling of saturn_demoouter — reverse mount lookup translated"
note "the inside-path bind source (/root/demoouter/demosub) to the host path."

banner "2e — Inspect saturn_demosub to see the translated mounts"
show 'docker inspect saturn_demosub  (workspace mount)'
docker inspect saturn_demosub --format '{{range .Mounts}}{{printf "  %s  ->  %s\n" .Source .Destination}}{{end}}'
note "The .Source column holds HOST paths — not /root/... inside-paths. That"
note "only works because the inner saturn asked the engine for its own parent"
note "container's mounts and replayed the translation."

# ───── ACT 3 — deploy you can debug from a dev container ─────────────

banner "ACT 3 — Deploy you can debug from a dev container"
note "The real pitch: saturn's translation runs the SAME codepath whether"
note "you invoke it from your laptop, from CI, or from inside another saturn"
note "container. If your deployment process is \"run saturn on these compose"
note "files,\" that process can be rehearsed and debugged from a dev container"
note "with (almost) no distortion — the nested run produces the same kind of"
note "compose.json the prod-host invocation would, and hands it to the same"
note "engine API."

banner "3a — No ownership distortion across the boundary"
note "Container runs as root; rootless userns maps container-uid 0 → host user."
note "So files a deploy script creates from inside land on host owned by you —"
note "editable, committable, no chown dance. A config generator or state-file"
note "writer behaves identically in dev and on a rootless prod host."
show "saturn exec -T dev bash -c 'date > /root/demoouter/from_inside.txt'"
(cd "$DEMO_ROOT/demoouter" && "$SATURN_BIN" exec -T dev bash -lc 'date > /root/demoouter/from_inside.txt; echo "(wrote as $(whoami), uid=$(id -u))"')
show "ls -l $DEMO_ROOT/demoouter/from_inside.txt"
ls -l "$DEMO_ROOT/demoouter/from_inside.txt"

banner "3b — Two saturn invocations, two compose.json, same shape"
note "demoouter/.saturn/compose.json was written by your HOST saturn (Act 2b)."
note "demosub/.saturn/compose.json   was written by the NESTED saturn (Act 2c)."
note "Both list bind sources as absolute HOST paths — translation already done."
note "If this demosub workspace were the thing your deploy script pushed to"
note "prod, the compose.json a prod host would generate would be shape-identical"
note "(different host paths, same translation). That's the \"debuggable in dev\""
note "claim: same logic, same output format, same engine hand-off."
show "python3 -c 'show volumes from both compose.json files'"
python3 - <<PY
import json, os
for label, path in (
    ("host-saturn  (Act 2b)", "$DEMO_ROOT/demoouter/.saturn/compose.json"),
    ("nested-saturn (Act 2c)", "$DEMO_ROOT/demoouter/demosub/.saturn/compose.json"),
):
    with open(path) as f:
        spec = json.load(f)
    vols = spec["services"]["dev"]["volumes"]
    print(f"  {label}:")
    for v in vols:
        print(f"    {v['source']:55s}  ->  {v['target']}")
    print()
PY
note "Note both lists are absolute host paths (no /root/… leaked through),"
note "and both are vanilla compose long-form volume entries. Either file can"
note "be fed to a bare \`docker compose -f compose.json up\` on any host whose"
note "engine can see those paths — dev, CI, or prod."

banner "Done — cleaning up"
