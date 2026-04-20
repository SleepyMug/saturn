# Boundary: SATURN_* env vars for nesting

> The env contract that lets saturn inside a saturn container create siblings on the host engine — distinct from the *host* shell's env, whose role is limited.

## Overview

When saturn launches a container (`up` or `project shell`), it injects a specific block of `SATURN_*` environment variables. Inside, saturn re-reads those on startup and derives its own behavior, allowing nested invocations to act on the host engine transparently. The same env var *names* appear on the host and inside a container but play different roles — the inside role must not leak into host-shell defaults.

## Both sides' perspective

### Host shell (user's interactive terminal)

Vars the user may set to customize host-side saturn:

- `SATURN_ENGINE=podman|docker` — host engine family (default `podman`). Only picks the default socket path; saturn always shells to `docker`.
- `SATURN_SOCK=<path>` — explicit socket path override.
- `SATURN_BASE_IMAGE=<ref>` — override base image tag.
- `SATURN_BASE_CONTAINERFILE=<path>` — override base Containerfile source; must still `COPY saturn /usr/local/bin/saturn`.

Vars the user must NOT set on the host:

- `SATURN_SUDO` — if `1` on the host, saturn will prepend `sudo` to every docker call, which breaks rootless setups.
- `SATURN_HOST_SOCK` — meaningful only inside a container; the default (`SOCK`) is correct for host.
- `SATURN_PROJECT` — meaningful only inside (sets runtime-command scope); setting on host does nothing useful and invites confusion. Rejected as an ambient default in [decisions/0001-single-file-distribution.md](../decisions/0001-single-file-distribution.md) (see also the note about "fake simplicity" in the user-decision history).

### Inside a saturn container

saturn injects these in `_project_env_flags(p)`:

| Var | Value at injection | Role inside |
|---|---|---|
| `SATURN_ENGINE` | `docker` | Socket default path is `/run/user/<uid>/docker.sock` — unused inside since `SATURN_SOCK` is always explicit. |
| `SATURN_SOCK` | `/var/run/docker.sock` | Inside path of the bind-mounted host socket; inside saturn sets `DOCKER_HOST=unix://...` from this. |
| `SATURN_HOST_SOCK` | `<host's SOCK>` | **Host-side** path. When the nested saturn creates another child container, this is the path it bind-mounts. Without propagating this, double-nesting would break (the inner saturn would try to bind-mount `/var/run/docker.sock`, which is a container path, not a host path). |
| `SATURN_SUDO` | `1` | Causes `_engine_cmd` to prepend sudo; required because `agent` can't open the socket directly. |
| `SATURN_PROJECT` | `<name>` | Project identity; `runtime info`/`init` key off this to find the ws mount. |

## Data representation at the boundary

Plain env vars (strings) passed via `docker run -e KEY=VALUE`. No escaping concerns — saturn's values are known-safe (paths, `0`/`1`, enum strings, validated project names).

## Ownership and lifecycle

- Host saturn owns the *contents* of each var and injects a fresh set on every `docker run` (no persistence).
- Inside saturn reads them once at module import; after that they're effectively immutable for that process.
- `SATURN_HOST_SOCK` is the only var whose value is the host-side path — all others are inside-relative. This asymmetry is load-bearing for double-nesting.

## Constraints per side

### Host constraints

- Must never have `SATURN_SUDO=1` or `SATURN_HOST_SOCK` leaked from a previous `saturn shell` session. The docker CLI's `-e` mechanism doesn't back-propagate; the host's shell env is independent of the container's.
- If the user wants to change socket targets, set `SATURN_SOCK` and optionally `SATURN_ENGINE`, nothing else.

### Container constraints

- `SATURN_PROJECT` is inside-only (consumed by `runtime info`/`init`). Host commands take `<name>` positionally; they don't consult `SATURN_PROJECT`.
- `SATURN_HOST_SOCK` is meaningful only as the host-side path string. If saturn inside ever tries to open it for itself (rather than re-bind-mount it), it'll fail — inside has no access to a path at `/run/user/1000/...` on the host namespace.
- These vars must not override the inside-side defaults for `SOCK` or `USE_SUDO`; saturn's module-level init honors them verbatim. A user who `-e SATURN_SUDO=0`'s an exec into a running container can accidentally break its ability to hit the socket.
