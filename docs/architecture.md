# Architecture

> One Python file. Logical components communicate through small helpers; all state lives in engine objects + host directories under `$HOME/saturn/`.

## Overview

saturn is a thin orchestrator over the `docker` CLI. It owns no state of its own: project identity comes from the host directory `$HOME/saturn/<name>/`; container and image identity comes from deterministic naming. The script is one file on disk, distributed as-is (`curl | chmod +x`), and the base image embeds a copy of the same script at `/usr/local/bin/saturn` so nesting is just running saturn again.

## Logical layering

```
┌──────────────────────────── cli ────────────────────────────┐
│ argparse tree + main() + exec-argv intercept                │
└───────┬──────────┬──────────┬──────────┬───────────────────┘
        │          │          │          │
        ▼          ▼          ▼          ▼
   ┌─────────┐ ┌─────────┐ ┌────────┐ ┌────────────┐
   │ project │ │   base  │ │ mixins │ │  runtime   │
   │  model  │ │  image  │ │        │ │  helpers   │
   └────┬────┘ └────┬────┘ └────┬───┘ └─────┬──────┘
        │           │           │           │
        └───────────┴─────┬─────┴───────────┘
                          ▼
                  ┌───────────────┐
                  │  engine ops   │
                  │ (subprocess)  │
                  └───────────────┘
                          │
                          ▼
              DOCKER_HOST → host engine socket
```

- **cli** ([components/cli](components/cli/index.md)) — argparse subparser tree and a `sys.argv` intercept so `saturn exec <name> <cmd...>` doesn't have its flags consumed by argparse.
- **project model** ([components/project](components/project/index.md)) — the `Project` class derives resource names (`saturn_<name>`, `localhost/saturn-<name>:latest`, `$HOST_HOME/saturn/<name>`) from a single `<name>`. `project_list()` unions directory children of `$HOST_HOME/saturn/` with containers labelled `saturn.project`.
- **base image** ([components/base-image](components/base-image/index.md)) — the saturn-base image is built from a HEAD + TAIL inlined Containerfile with mixin `RUN` lines spliced between. At build time, a temp dir is assembled containing the rendered Containerfile + a copy of the running saturn script (for the `COPY saturn` step).
- **mixins** ([components/mixins](components/mixins/index.md)) — inlined registry of (HOME-relative paths + setup snippet) bundles. Used by `base template`/`default` (splice setup lines) and `up` (bind-mount selected paths at `$HOST_HOME/<rel>`).
- **runtime helpers** — `ensure_base()`, `check_socket()`, `container_status()`, plus `_env_flags()` / `_base_mount_flags()` / `_mixin_mount_flags()` / `_check_mixin_paths()` for launching new containers.
- **engine ops** ([components/engine](components/engine/index.md)) — every engine call is `docker <args>` (no sudo, never `podman`). The docker CLI speaks both engines' docker-compat API.

## Key data flows

### `saturn new <name>`

1. `mkdir -p $HOST_HOME/saturn/<name>`.
2. If `.saturn/Containerfile` doesn't exist, seed it with the inlined template (`FROM localhost/saturn-base:latest` + a commented RUN example).
3. Print next-step hint.

No engine calls — this is a pure host-filesystem operation. Works identically on host or inside a saturn container (the bind-mount of `$HOST_HOME` makes the new directory visible on both sides).

### `saturn up <name> [--mixins <csv>]` — build + launch

1. Resolve mixins (`_cli_mixins` → defaults when flag omitted); `_check_mixin_paths` verifies every selected mixin path exists on the host, else exits.
2. Verify `$HOST_HOME/saturn/<name>/` exists; `check_socket()`; `ensure_base()` (build saturn-base if missing).
3. If `.saturn/Containerfile` is present: `docker build -f $HOST_HOME/saturn/<name>/.saturn/Containerfile -t localhost/saturn-<name>:latest $HOST_HOME/saturn/<name>`. Otherwise run directly from the base image.
4. Start the container: `docker run -d --init --name saturn_<name> --label saturn.project=<name> -v $HOST_HOME/saturn:$HOST_HOME/saturn -v $HOST_SOCK:/var/run/docker.sock <mixin -v pairs> -e SATURN_*=... -e HOME=$HOST_HOME -w $HOST_HOME/saturn/<name> <image>`.

### `saturn exec <name> <cmd...>` — in the project container

1. Verify container is running (`container_status`).
2. `os.execvp("docker", ["exec", "-it", "saturn_<name>", *cmd])` — saturn exits, the user's command takes over.

### Nesting

Inside a saturn container:

- `DOCKER_HOST=unix:///var/run/docker.sock` points at the bind-mounted host socket.
- `SATURN_HOST_SOCK` holds the host-side socket path; used as the bind-mount *source* when inner saturn spawns siblings.
- `SATURN_HOST_HOME` holds the host-side `$HOME`; used both as the base for the projects-root mount (`$SATURN_HOST_HOME/saturn`) and for each mixin path (`$SATURN_HOST_HOME/<rel>`) when a sibling is launched.
- `HOME` is set to `$SATURN_HOST_HOME` so `~/.ssh`, `~/.claude.json`, etc. resolve to the bind-mounted host paths automatically — **provided** the relevant mixin is mounted both in the outer and inner invocation (the existence check runs against the inside view).

From saturn's perspective, inside-operations look identical to host; containers it creates are host-engine siblings of its own container.

## Execution-context constraints

- **No daemon**. saturn is stateless between invocations — each `saturn <cmd>` is a fresh process, and state lives entirely in engine objects + host directories.
- **stdlib only**. No third-party Python deps; the script runs on any image with `python3`.
- **Rootless engine strongly preferred**. Running as container-root works under any engine, but the ownership ergonomics (files on disk owned by host-you, not root) depend on rootless userns. Rootful engines still function; files written from inside would be owned by host root.
- **Docker classic builder on podman only**. `DOCKER_BUILDKIT=0` is forced when `ENGINE == "podman"` because podman's docker-compat socket doesn't serve BuildKit. On docker (including rootless), BuildKit is left enabled.
