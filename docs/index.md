# saturn

> Single-file Python wrapper over `docker compose` for rootless podman/Docker. A workspace is any directory with `.saturn/compose.yaml`. Saturn passes the compose spec through a translation step (env resolution, reverse mount lookup in guest mode) and forwards to `docker compose`. Works at any nesting level without propagating per-path env vars.

## Overview

Saturn is a thin wrapper over `docker compose`. Two saturn-specific commands seed templates (`new`) and manage the base image (`base`); everything else is forwarded to compose after a translation pass. The translation is the value-add: inside a saturn container, saturn inspects its own container through the bind-mounted engine socket, learns the current container's mount list, and rewrites every bind-mount source in the child compose file from an inside-path into the real host path. This lets nested `saturn up` / `saturn exec` / etc. work deterministically without needing a registry of workspaces or per-mixin env var propagation — `docker inspect <self>` is the single source of truth.

The container runs as root; rootless userns maps that back to your host user, so files written from inside land on disk with host-user ownership.

Implementation: source under `src/saturn/` (split into `cli`, `env`, `workspace`, `base`, `engine`, `docker` modules), distributed as a single-file `python -m zipapp` artifact at repo root (`./saturn`). Stdlib only. Hard dep on the `docker` CLI with the `compose` plugin — everything flows through `DOCKER_HOST=unix://...`. See [decision 0018](decisions/0018-modular-source-zipapp-distribution.md).

## Sub-documents

- [architecture.md](architecture.md) — Compose as the IR; translate-then-forward as the single saturn-specific step. Multi-file input (base + overrides) layered via compose's native `-f` chain.
- [components/cli/](components/cli/index.md) — Small `main()` switch: `new`, `base`, `shell`, `docker`, pass-through.
- [components/workspace/](components/workspace/index.md) — The `.saturn/` directory: what `saturn new --<flag>` seeds; how workspaces are discovered (walk cwd upward).
- [components/base-image/](components/base-image/index.md) — Minimal Debian base with `docker` CLI + compose plugin + `python3`/`git`/`curl`. Tool-specific installs (ssh/gh/node) moved to per-workspace Dockerfiles.
- [components/engine/](components/engine/index.md) — The translation pipeline: `docker compose config --format json`, `_current_container_mounts`, `_translate`, `_translate_compose`, `passthrough`.
- [boundaries/engine-socket.md](boundaries/engine-socket.md) — Bind-mounted `/var/run/docker.sock`: container-root ↔ host-user under rootless userns.
- [boundaries/nested-env.md](boundaries/nested-env.md) — Two-var contract (`SATURN_IN_GUEST`, `SATURN_SOCK`). Everything else saturn needs for nesting is derived by engine inspect.
- [decisions/](decisions/) — numbered, append-only design decisions (0001–0018)
- [plans/](plans/) — pending work
- [experiment_journal/](experiment_journal/) — findings about external-world behavior
