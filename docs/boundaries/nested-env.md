# Boundary: SATURN_* env vars for nesting

> The env contract that lets saturn inside a saturn container create siblings on the host engine — distinct from the *host* shell's env, whose role is limited.

## Overview

When saturn launches a container (`saturn up`), it injects a specific block of `SATURN_*` environment variables. Inside, saturn re-reads those on startup and derives its own behavior, so nested invocations act on the host engine transparently. Four families of vars cross this boundary: host-side socket/home (for nested socket access + mixin default fallbacks), the workspace pair (host-side + container-side paths of the current workspace — load-bearing for `_resolve_target`), per-slot host paths (one per mixin slot, load-bearing for nested mixin mounts), and the host-vs-guest flag.

## Both sides' perspective

### Host shell (user's interactive terminal)

Vars the user may set to customize host-side saturn:

- `SATURN_ENGINE=podman|docker` — host engine family (default `podman`). Only picks the default socket path; saturn always shells to `docker`.
- `SATURN_SOCK=<path>` — explicit socket path override.
- `SATURN_BASE_IMAGE=<ref>` — override base image tag.
- `SATURN_HOST_HOME=<path>` — override the host-side home (default `$HOME` / `Path.home()`). Normally unnecessary.
- `SATURN_MIXIN_<SLOT>=<path>` — override a mixin slot's host-side source path. When unset on host, defaults to `$HOST_HOME/<default_host>` from the slot schema. Example: `SATURN_MIXIN_CLAUDE_JSON=/some/scratch/claude.json` gives the container an isolated `~/.claude.json`.

Vars the user should NOT set on the host:

- `SATURN_HOST_SOCK` — meaningful only inside a container; the default (`SOCK`) is correct for host.
- `SATURN_HOST_WORKSPACE` / `SATURN_WORKSPACE` — meaningful only inside a container. The outer saturn sets them per-container; setting them on the host shell is stale and confusing.
- `SATURN_IN_GUEST` — meaningful only inside a container. Setting it on host would disable host-mode auto-create and require every mixin env var explicitly.

### Inside a saturn container

saturn injects these via `_env_flags(slots, workspace)`:

| Var | Value at injection | Role inside |
|---|---|---|
| `SATURN_ENGINE` | the host's `ENGINE` (e.g. `podman` or `docker`) | Identifies the actual engine family behind the bind-mounted socket. Drives the BuildKit-vs-classic-builder decision inside. |
| `SATURN_SOCK` | `/var/run/docker.sock` | Inside path of the bind-mounted host socket; inside saturn sets `DOCKER_HOST=unix://...` from this. |
| `SATURN_HOST_SOCK` | `<host's SOCK>` | **Host-side** path. When the nested saturn creates another child container, this is the path it bind-mounts. Without propagating, double-nesting would try to bind-mount `/var/run/docker.sock` (a container path) on the host. |
| `SATURN_HOST_HOME` | `<host's HOST_HOME>` | **Host-side** `$HOME` path. Informational + used as the prefix for mixin default fallbacks (moot in guest mode since defaults don't apply there). |
| `SATURN_HOST_WORKSPACE` | `workspace.host_path` | **Host-side** absolute path of the current workspace. Consumed by inner `_resolve_target` to compute a target's host path: `host_path = SATURN_HOST_WORKSPACE / rel`. |
| `SATURN_WORKSPACE` | `workspace.container_dir` (e.g. `/root/<name>`) | **Container-side** path of the current workspace. Consumed by inner `_resolve_target` to validate a target is under it and to compute `rel = target.relative_to(SATURN_WORKSPACE)`. |
| `SATURN_IN_GUEST` | `1` | Presence ⇒ inside a container (`IS_HOST=False`). Inner saturn switches to guest mode: mixin slot env vars are required, host-mode defaults and auto-create are disabled. |
| `SATURN_MIXIN_<SLOT>` | `<host_path>` for each resolved slot of each selected mixin | **Host-side** path. Used as the bind-mount *source* when inner saturn spawns a sibling with the same mixin. One var per slot; outer saturn injects only the slots for mixins it selected. |

Note: `HOME` is **not** injected. The container's `HOME` stays at the image default (`/root`). Both the workspace dir (`/root/<name>`) and mixin targets (`/root/.ssh`, `/root/.claude`, `/root/.claude.json`, `/root/.codex`, `/root/.config/gh`) live under `/root/`, so `~/<name>` and `~/.ssh` etc. resolve naturally.

## Data representation at the boundary

Plain env vars (strings) passed via `docker run -e KEY=VALUE`. No escaping concerns — saturn's values are known-safe (paths, enum strings, validated workspace basenames). `SATURN_IN_GUEST` is set to the literal string `"1"`.

## Ownership and lifecycle

- Host saturn owns the *contents* of each var and injects a fresh set on every `docker run` (no persistence).
- Inside saturn reads them once at module import; after that they're effectively immutable for that process.
- The host-side paths (`SATURN_HOST_SOCK`, `SATURN_HOST_HOME`, `SATURN_HOST_WORKSPACE`, `SATURN_MIXIN_<SLOT>`) and the container-side pair (`SATURN_WORKSPACE`) are load-bearing for nesting: inner saturn consumes them directly. `SATURN_IN_GUEST` is pure signalling.

## Constraints per side

### Host constraints

- Must not have `SATURN_HOST_SOCK`, `SATURN_HOST_WORKSPACE`, `SATURN_WORKSPACE`, or `SATURN_IN_GUEST` leaked from a previous inside session. `SATURN_HOST_SOCK` normally defaults to `SOCK` (harmless); a stale `SATURN_IN_GUEST=1` would flip host-mode off, require every mixin env var explicitly, and make every `saturn up` fail because the workspace vars aren't set on host.
- `SATURN_HOST_HOME` defaults to `Path.home()`. Override only if you know why.
- `SATURN_MIXIN_<SLOT>` values are optional on host; defaults fall back to `$HOST_HOME/<default_host>`. Missing host paths are auto-created (dir or file per the slot's `kind`).

### Container constraints

- `SATURN_HOST_SOCK`, `SATURN_HOST_HOME`, and `SATURN_HOST_WORKSPACE` are meaningful only as host-side paths. Inner saturn never `open()`s them directly — they're only bind-mount sources.
- `SATURN_MIXIN_<SLOT>` values are host-side paths; inside saturn uses them only as bind-mount sources when spawning a sibling. They are not resolvable from inside the container's filesystem namespace.
- With `SATURN_IN_GUEST=1`, `_resolve_mixin_slots` requires every selected mixin's slot env vars and `_resolve_target` requires a target path under `SATURN_WORKSPACE`. Missing → exit with a labelled message. No filesystem writes.
