# 0016 — Adaptive BuildKit + CLI/backend mismatch check at import

> Replaces the unconditional `DOCKER_BUILDKIT=0` from [0012](0012-compose-native-wrapper.md).
> Saturn now probes the `docker` binary and the engine at `$SATURN_SOCK`
> once at import, then (a) fails fast when podman CLI is pointed at a
> non-podman backend and (b) lets docker's default BuildKit win when both
> CLI and backend are docker-cli / rootless Docker.

## Context

0012 decided saturn would force `DOCKER_BUILDKIT=0` for everyone because
podman's docker-compat socket doesn't serve the BuildKit API. At the
time, podman was the only supported backend; "harmless on Docker (falls
back to classic)" covered the docker case.

Two things changed the balance:

1. **Rootless Docker is a first-class supported backend.** Its
   `unix://$XDG_RUNTIME_DIR/docker.sock` socket serves the full Docker
   Engine API, including BuildKit. Forcing classic builder there loses
   BuildKit's cache/speed advantages for no reason.
2. **Users point `docker` at podman via the `podman-docker` package or
   shell shims** (`docker() { podman --remote --url="$DOCKER_HOST" "$@"; }`).
   If the backend at `$SATURN_SOCK` is dockerd (not podman), podman
   fails the first engine call with `Error: unable to connect to Podman
   socket: ping response was 404` — an opaque error buried inside
   compose's output. Saturn can detect this combination before the first
   engine call and exit with a crisper message.

A single small detection pass at import is sufficient for both.

## Decision

Two probe helpers run at module top (replacing the prior inline rootful
warning + unconditional `DOCKER_BUILDKIT=0` setdefault).

**`_detect_cli() -> str`** — parse `docker --version` stdout:
`"Docker version …"` → `"docker"`; `"podman"` → `"podman"`; anything
else → `"unknown"`. This correctly identifies podman shims (the real
podman binary's banner bleeds through). It cannot distinguish local
podman from `podman --remote` — the banners are byte-identical — but
the distinction doesn't matter: the mismatch manifests the same way
either way.

**`_detect_backend() -> tuple[str, bool]`** — run plain `docker version`
(no `--format`) through the socket with `timeout=2`. Substring
`"Podman Engine"` in stdout → `"podman"`; clean exit without that
string → `"docker"`; any failure → `"unknown"`. The `--format
'{{json .Server.Components}}'` form was tempting but fails under
podman's own CLI, which lacks a `.Components` field in its version
struct. Podman's docker-compat response contains both a `Podman Engine`
and a generic `Engine` component — so the positive substring test is
the robust discriminator, not the absence of `"Engine"`.

The helper also consolidates the prior rootful-engine warning (socket
`st_uid == 0` while caller is not root), returning the `root_owned`
flag as a side value. Rootful-engine detection is only reliable on
host: inside a rootless-userns guest, the socket appears root-owned
from `stat()`'s view even against a rootless backend.

Wiring at module top:

- **Check A (fail-fast).** `cli == "podman"` and `backend != "podman"`
  → `sys.exit` with a message naming `$SATURN_SOCK` and the backend
  value observed. Fires on any failure to confirm a podman backend
  under podman CLI — including the 404 mismatch (`backend="unknown"`)
  and an explicit docker response (`backend="docker"`). The call
  cannot succeed; an advisory warning would just delay the same error.
- **Check B (adaptive buildkit).** `cli == "docker"` and
  `backend == "docker"` and not (`IS_HOST` and `root_owned`) →
  `os.environ.pop("DOCKER_BUILDKIT", None)` so docker's own default
  (BuildKit) wins. Every other combination keeps
  `setdefault("DOCKER_BUILDKIT", "0")`.

Opt-out: `SATURN_SKIP_ENGINE_PROBE=1` skips both probes and keeps
`DOCKER_BUILDKIT=0`. Users who want the pre-0016 behavior can export it
in their shell.

## Consequences

- **BuildKit works out of the box on rootless Docker.** No user-visible
  config change; saturn just stops suppressing it. `saturn up --build`
  on a rootless-docker host gets cache mounts, parallel stages, and
  modern build output.
- **Podman CLI × docker backend fails with a clear error.** Instead of
  an opaque `ping response was 404` inside a compose log, the user
  sees `saturn: podman CLI cannot talk to the engine at <sock>` at the
  very first saturn invocation.
- **Two subprocess calls per saturn invocation.** Measured at <100 ms
  combined against a local socket. Acceptable for a fresh-process CLI;
  disable with `SATURN_SKIP_ENGINE_PROBE=1` if the cost matters.
- **The rootful-socket warning moves into `_detect_backend`.** Behavior
  unchanged from `saturn:85-95` (advisory, host-only, silent on stat
  failure); location is more natural now that there's a single probe
  site.
- **Guest-mode BuildKit is optimistic.** When saturn runs inside a
  saturn container, `stat(sock).st_uid` is unreliable under rootless
  userns — it reads 0 regardless of the backing daemon's rootful/rootless
  status. Check B's `IS_HOST and root_owned` gate means guest mode
  always enables BuildKit on docker backend. This matches expectations:
  the outer saturn already vetted the engine; inside, the backend probe
  is the authoritative signal.

## Rejected alternatives

- **`SATURN_CLI=docker|podman` explicit selector.** Double surface with
  no user value. Auto-detection is reliable — the only case it can't
  resolve (remote vs local podman) isn't an input to either check.
- **Probe via `docker info` or `docker version --format '{{json
  .Server.Components}}'`.** The JSON-template form fails under
  podman's own CLI (`can't evaluate field Components in type
  *define.Version`). Plain `docker version` stdout works under both.
- **Silently fail Check A (warn-and-continue like the rootful case).**
  Rootful × rootless is a spectrum of cost/security, not a hard break;
  warning + proceed is defensible. Podman-CLI × docker-backend is a
  hard break — the first API call returns 404 — so warning just delays
  the same error with less context.
- **Cache probe results on disk.** Saturn is a fresh process every
  invocation; a cache file is more state than the 50 ms of probe cost
  is worth. `SATURN_SKIP_ENGINE_PROBE=1` is the escape hatch for users
  who disagree.
- **Set `DOCKER_BUILDKIT=1` explicitly on rootless docker.** `pop` is
  better for two reasons: (a) lets users' shell-exported `=0` still
  win; (b) in nested saturn, the inner starts without the pinned `1`
  and can re-decide. Matches the "trust docker's default" intent.
