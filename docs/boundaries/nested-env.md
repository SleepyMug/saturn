# Boundary: SATURN_* env vars for nesting

> The env contract that lets saturn inside a saturn container create siblings on the host engine — distinct from the *host* shell's env, whose role is limited.

## Overview

When saturn launches a container (`saturn up`), it injects a specific block of `SATURN_*` + `HOME` environment variables. Inside, saturn re-reads those on startup and derives its own behavior, so nested invocations act on the host engine transparently. Two variables hold **host-side** paths and are the load-bearing pieces for nesting: `SATURN_HOST_SOCK` and `SATURN_HOST_HOME`.

## Both sides' perspective

### Host shell (user's interactive terminal)

Vars the user may set to customize host-side saturn:

- `SATURN_ENGINE=podman|docker` — host engine family (default `podman`). Only picks the default socket path; saturn always shells to `docker`.
- `SATURN_SOCK=<path>` — explicit socket path override.
- `SATURN_BASE_IMAGE=<ref>` — override base image tag.
- `SATURN_HOST_HOME=<path>` — override the host-side home (default `$HOME` / `Path.home()`). Normally unnecessary.

Vars the user should NOT set on the host:

- `SATURN_HOST_SOCK` — meaningful only inside a container; the default (`SOCK`) is correct for host.

### Inside a saturn container

saturn injects these via `_env_flags()`:

| Var | Value at injection | Role inside |
|---|---|---|
| `SATURN_ENGINE` | the host's `ENGINE` (e.g. `podman` or `docker`) | Identifies the actual engine family behind the bind-mounted socket. Drives the BuildKit-vs-classic-builder decision inside. |
| `SATURN_SOCK` | `/var/run/docker.sock` | Inside path of the bind-mounted host socket; inside saturn sets `DOCKER_HOST=unix://...` from this. |
| `SATURN_HOST_SOCK` | `<host's SOCK>` | **Host-side** path. When the nested saturn creates another child container, this is the path it bind-mounts. Without propagating, double-nesting would try to bind-mount `/var/run/docker.sock` (a container path) on the host. |
| `SATURN_HOST_HOME` | `<host's HOST_HOME>` | **Host-side** `$HOME` path. Used both as the bind-mount source for `-v HOST_HOME:HOST_HOME` on siblings and as the value of `HOME` inside (see next row). |
| `HOME` | `<host's HOST_HOME>` | Override container-root's default `HOME=/root` so `~/.ssh`, `~/.claude.json`, `~/.config/gh`, etc. resolve to the bind-mounted host home. |

## Data representation at the boundary

Plain env vars (strings) passed via `docker run -e KEY=VALUE`. No escaping concerns — saturn's values are known-safe (paths, enum strings, validated project names).

## Ownership and lifecycle

- Host saturn owns the *contents* of each var and injects a fresh set on every `docker run` (no persistence).
- Inside saturn reads them once at module import; after that they're effectively immutable for that process.
- `SATURN_HOST_SOCK` and `SATURN_HOST_HOME` are the only vars whose values are host-side paths — all others are inside-relative. This asymmetry is load-bearing for nesting.

## Constraints per side

### Host constraints

- Must not have `SATURN_HOST_SOCK` leaked from a previous inside session. Normally no-op since `SATURN_HOST_SOCK` defaults to `SOCK`, but an explicit stale value would be wrong.
- `SATURN_HOST_HOME` defaults to `Path.home()`. Override only if you know why.

### Container constraints

- `SATURN_HOST_SOCK` and `SATURN_HOST_HOME` are meaningful only as host-side paths. If inside saturn ever tries to `open()` them directly (rather than re-bind-mount), it'll fail — inside has no access to a path at `/run/user/1000/...` on the host namespace (the mount only covers `$HOME`, not the runtime dir).
- These vars must not override the inside-side defaults for `SOCK`; saturn's module-level init honors them verbatim.
