# Architecture

> One Python file. Logical components communicate through small helpers; saturn owns no state of its own.

## Overview

saturn is a thin orchestrator over the `docker` CLI. A *workspace* is any directory containing a `.saturn/` marker — saturn doesn't keep a registry or a fixed root for them. Container and image identity are derived from the workspace's basename. The script is one file on disk, distributed as-is (`curl | chmod +x`), and the base image embeds a copy of the same script at `/usr/local/bin/saturn` so nesting is just running saturn again.

## Logical layering

```
┌──────────────────────────── cli ────────────────────────────┐
│ argparse tree + main() + exec-argv intercept                │
└───────┬──────────┬──────────┬──────────┬───────────────────┘
        │          │          │          │
        ▼          ▼          ▼          ▼
   ┌──────────┐ ┌─────────┐ ┌────────┐ ┌────────────┐
   │workspace │ │  base   │ │ mixins │ │  runtime   │
   │  model   │ │  image  │ │        │ │  helpers   │
   └────┬─────┘ └────┬────┘ └────┬───┘ └─────┬──────┘
        │            │           │           │
        └────────────┴─────┬─────┴───────────┘
                           ▼
                   ┌───────────────┐
                   │  engine ops   │
                   │ (subprocess)  │
                   └───────────────┘
                           │
                           ▼
               DOCKER_HOST → host engine socket
```

- **cli** ([components/cli](components/cli/index.md)) — argparse subparser tree and a `sys.argv` intercept so `saturn exec <cmd...>` doesn't have its flags consumed by argparse.
- **workspace model** ([components/workspace](components/workspace/index.md)) — the `Workspace` class bundles `(host_path, view_path, name, container, image, container_dir)` derived from a target directory. `_resolve_target(arg)` turns a CLI target (or cwd) into a `Workspace`, consuming `SATURN_HOST_WORKSPACE` / `SATURN_WORKSPACE` inside a container to compute the host path from the target's relative position under the current workspace.
- **base image** ([components/base-image](components/base-image/index.md)) — the saturn-base image is built from a HEAD + TAIL inlined Containerfile with mixin `RUN` lines spliced between. At build time, a temp dir is assembled containing the rendered Containerfile + a copy of the running saturn script (for the `COPY saturn` step).
- **mixins** ([components/mixins](components/mixins/index.md)) — inlined registry of (slot records + setup snippet) bundles. Each slot has `env`/`target`/`kind`/`default_host`. Used by `base template`/`default` (splice setup lines) and `up` (bind-mount each slot as `<host_path>:<target>`, with `host_path` resolved per-slot from env var or host-mode default).
- **runtime helpers** — `ensure_base()`, `check_socket()`, `container_status()`, plus `_env_flags(slots, workspace)` / `_base_mount_flags()` / `_mixin_mount_flags(slots)` / `_resolve_mixin_slots()` / `_ensure_mixin_host_paths()` for launching new containers.
- **engine ops** ([components/engine](components/engine/index.md)) — every engine call is `docker <args>` (no sudo, never `podman`). The docker CLI speaks both engines' docker-compat API.

## Key data flows

### `saturn new [dir]`

1. `mkdir -p <dir>` (default cwd).
2. Build a `Workspace` via `_resolve_target`.
3. `mkdir -p <dir>/.saturn`; if `.saturn/Containerfile` is absent, seed it from the inlined template.

No engine calls — pure filesystem. Works on host or inside a saturn container (for subdirs of the current workspace, since those are under the bind-mount).

### `saturn up [dir] [--mixins <csv>] [--mixin-root <dir>]` — build + launch

1. `ws = _resolve_target(dir)`. Host: `host_path == view_path == resolve(dir)`. Guest: requires `dir` to be under `SATURN_WORKSPACE`; host path is `SATURN_HOST_WORKSPACE + rel`.
2. `check_socket()`.
3. Short-circuit: if `saturn_<name>` is already running, print "already up" and return.
4. Resolve mixin slots (`_resolve_mixin_slots(names, mixin_root)`). Host-mode auto-creates missing slot paths (`_ensure_mixin_host_paths`).
5. `ensure_base()` — build saturn-base if missing.
6. If `<view_path>/.saturn/Containerfile` exists: `docker build -f <host_path>/.saturn/Containerfile -t localhost/saturn-<name>:latest <host_path>`. Otherwise run directly from the base image.
7. If a stopped `saturn_<name>` exists, `docker rm -f` it.
8. Start the container: `docker run -d --init --name saturn_<name> --label saturn.workspace=<host_path> -v $HOST_SOCK:/var/run/docker.sock -v <host_path>:/root/<name> <one "-v <host_path>:<target>" per slot> -e SATURN_ENGINE=... -e SATURN_SOCK=/var/run/docker.sock -e SATURN_HOST_SOCK=... -e SATURN_HOST_HOME=... -e SATURN_HOST_WORKSPACE=<host_path> -e SATURN_WORKSPACE=/root/<name> -e SATURN_IN_GUEST=1 <one "-e <slot.env>=<host_path>" per slot> -w /root/<name> <image>`.

### `saturn exec <cmd...>` — in cwd's container

1. Resolve cwd's workspace → derive `saturn_<name>`.
2. Verify container is running (`container_status`).
3. `os.execvp("docker", ["exec", "-it", "saturn_<name>", *cmd])` — saturn exits, the user's command takes over.

### Nesting

Inside a saturn container (`IS_HOST=False`):

- `DOCKER_HOST=unix:///var/run/docker.sock` points at the bind-mounted host socket.
- `SATURN_HOST_SOCK` holds the host-side socket path; used as the bind-mount *source* when inner saturn spawns siblings.
- `SATURN_HOST_HOME` holds the host-side `$HOME`; used as the prefix for mixin default fallbacks (moot in guest mode).
- `SATURN_HOST_WORKSPACE` + `SATURN_WORKSPACE` are a pair: the former is the host-side path of the current container's workspace, the latter is the container-side path (e.g. `/root/<name>`). Inner `_resolve_target` consumes both to translate any target dir (under the current workspace) into a host path for sibling bind-mounts.
- `SATURN_IN_GUEST=1` puts inner saturn in guest mode: per-slot mixin env vars are required (no defaults, no auto-create) and targets outside the current workspace exit fail-fast.
- Each selected mixin's `SATURN_MIXIN_<SLOT>` holds the host-side path; inner saturn reads it directly and uses it as the bind-mount source for a sibling. Container `HOME` stays `/root`; the workspace lives at `/root/<name>` and mixin targets at `/root/.<tool>`.

From saturn's perspective, inside-operations talk to the host engine via the socket. Cross-workspace operations (`new` / `up` for a path outside the current workspace) are rejected fail-fast.

## Execution-context constraints

- **No daemon**. saturn is stateless between invocations — each `saturn <cmd>` is a fresh process, and state lives entirely in engine objects + host directories.
- **stdlib only**. No third-party Python deps; the script runs on any image with `python3`.
- **Rootless engine strongly preferred**. Running as container-root works under any engine, but the ownership ergonomics (files on disk owned by host-you, not root) depend on rootless userns. Rootful engines still function; files written from inside would be owned by host root.
- **Docker classic builder on podman only**. `DOCKER_BUILDKIT=0` is forced when `ENGINE == "podman"` because podman's docker-compat socket doesn't serve BuildKit. On docker (including rootless), BuildKit is left enabled.
