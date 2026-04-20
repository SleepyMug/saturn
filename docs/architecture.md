# Architecture

> One Python file. Logical components communicate through small, typed helpers; all state lives in engine-managed objects (images, volumes, containers).

## Overview

saturn is a thin orchestrator over `docker` CLI. It owns no state of its own: every fact about a project is recoverable by inspecting the engine's store. The script is one file on disk, distributed as-is (`curl | chmod +x`), and the base image it builds into embeds a copy of the same script at `/usr/local/bin/saturn` so nesting is just running saturn again.

## Logical layering

```
┌──────────────────────────── cli ────────────────────────────┐
│ argparse tree + main() + exec-argv intercept                │
└───────┬──────────┬──────────┬──────────┬────────────────────┘
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
- **project model** ([components/project](components/project/index.md)) — the `Project` class derives all resource names (`saturn_<name>`, `saturn_ws_<name>`, `localhost/saturn-<name>:latest`, `/home/agent/<name>`) from a single `<name>` input. `project_list()` queries the engine for volumes labelled `saturn.volume=ws`.
- **base image** ([components/base-image](components/base-image/index.md)) — the saturn-base image is built from an inlined Containerfile string (split into HEAD + TAIL, with mixin install lines spliced between); at build time, a temp dir is assembled containing the rendered Containerfile + a copy of the running saturn script (for the `COPY saturn` step).
- **mixins** ([components/mixins](components/mixins/index.md)) — inlined registry of user-global state bundles (install snippet + user-global volume + target path). Used by `base template`/`default` (splice install lines), `up` (mount volumes in project container), and `project config` (shell with only mixin volumes).
- **runtime helpers** — `ensure_base()`, `check_socket()`, `ensure_volume()`, `container_status()`, plus env-propagation helpers for launching new containers.
- **engine ops** ([components/engine](components/engine/index.md)) — all engine calls go through `_engine_cmd(*args)` which prepends `["sudo"]` if `SATURN_SUDO=1` is set, then always `["docker", *args]`. saturn never shells to `podman` directly; the docker CLI speaks both daemons' docker-compat API.

## Key data flows

### `saturn up <name>` — build + launch

1. Validate project exists (`project_exists` → `docker volume inspect saturn_ws_<name>`).
2. Check socket, `ensure_base()` (build saturn-base if missing).
3. Build project image from volume: `docker run --rm --init -v saturn_ws_<name>:/ctx -v $HOST_SOCK:/var/run/docker.sock saturn-base sudo docker build -f /ctx/.saturn/Containerfile -t localhost/saturn-<name>:latest /ctx` — a transient helper runs `docker build` *from inside*, so the build context is volume contents and the image lands in the host engine's store.
4. `docker run -d --init --name saturn_<name> -v saturn_ws_<name>:/home/agent/<name> -v $HOST_SOCK:/var/run/docker.sock -w /home/agent/<name> -e SATURN_*=... saturn-<name>:latest`.

### `saturn up <name> --mixins <csv>` — with user-state volumes

Additionally mount one volume per selected mixin at its target path. Engine-level effect: the `docker run -d ...` call includes a `-v saturn_mixin_<m>:<target>` or `--mount type=volume,source=saturn_mixin_<m>,target=<target>,volume-subpath=<subpath>` flag per mixin (see [components/mixins](components/mixins/index.md)). The mixin volumes are created on-demand (chowned to agent, subpath files pre-touched) the first time they're selected by any command.

### `saturn project config [--mixins <csv>]` — interactive state setup

Base-image shell with only the selected mixin volumes (plus the engine socket) mounted — no ws volume, no `SATURN_PROJECT`. Users run `ssh-keygen`, `gh auth login`, etc. to populate the user-global state. Defaults to all mixins when `--mixins` is omitted.

### `saturn exec <name> <cmd...>` — in the project container

1. Verify container is running (`container_status`).
2. `os.execvp("docker", ["exec", "-it", "saturn_<name>", *cmd])` — the current process is replaced, so saturn gets out of the way entirely for the user's command.

### `saturn put <name> <host-src> [<dst>]` — import files

1. Spin a transient helper: `docker run -d --init --name saturn_cp_<name> -v saturn_ws_<name>:/home/agent/<name> saturn-base sleep infinity`.
2. Resolve `<dst>`: absolute paths pass through; relative paths are rooted at `/home/agent/<name>/`.
3. `docker exec --user 0 saturn_cp_<name> mkdir -p <parent>`.
4. `docker cp <host-src> saturn_cp_<name>:<resolved-dst>` — raw src string, so trailing `/.` semantics survive.
5. `docker exec --user 0 saturn_cp_<name> chown -R 10001:10001 /home/agent/<name>` — fixes storage-level ownership so `agent` owns the new content.
6. `docker rm -f saturn_cp_<name>`.

`get` is the reverse and applies the same resolution to `<src>`: absolute paths are used as-is inside the helper, relative paths are rooted at the ws mount.

### Nesting

Inside a saturn container, `DOCKER_HOST=unix:///var/run/docker.sock` points at the bind-mounted host socket, `SATURN_SUDO=1` causes `_engine_cmd` to prepend sudo (container user is `agent`, not root; sudo → inside-uid 0 → host-uid-1000 under userns → socket owner match). From saturn's perspective the operations look identical to host; containers it creates are host-engine siblings of its own container.

## Execution-context constraints

- **No daemon**. saturn is stateless between invocations — each `saturn <cmd>` is a fresh process, and state lives entirely in engine objects.
- **stdlib only**. No third-party Python deps; the script runs on any image with `python3`.
- **rootless engine only**. saturn's sudo-based socket access assumes rootless podman or rootless docker (where inside-uid 0 maps back to the host socket owner). Rootful docker is not a first-class target — `sudo` to uid 0 inside still works because it maps to host uid 0 = socket owner (root), but the non-root-inside property becomes purely a defense-in-depth story rather than a privilege boundary.
- **Docker classic builder**. `DOCKER_BUILDKIT=0` is forced because podman's docker-compat socket doesn't serve the BuildKit API.
