# saturn

> Single-file Python CLI for rootless podman/Docker dev containers. A workspace is any directory with a `.saturn/` marker; `saturn up` bind-mounts it at `/root/<basename>` in a fresh container. Works at any nesting level.

## Overview

saturn manages per-workspace dev-container lifecycles on a rootless container engine. There is no project registry — any directory can be a workspace by having a `.saturn/` marker (created via `saturn new`). The container runs as root; rootless userns maps that back to your host user, so files written from inside land on disk with the right ownership.

A saturn container has the `docker` CLI wired to the host engine's socket and can itself invoke `saturn` — which creates **siblings** on the host engine. `SATURN_HOST_WORKSPACE` + `SATURN_WORKSPACE` + `SATURN_HOST_SOCK` propagate so the innermost saturn can still reach the host engine and bind-mount from real host paths, for any target under the current container's workspace.

Implementation: one Python file, stdlib-only. All engine mutations flow through the host's docker-compat socket (`DOCKER_HOST=unix://...`) so concurrent saturn invocations are serialized by the single service process.

## Sub-documents

- [architecture.md](architecture.md) — One Python file. Logical components communicate through small helpers; saturn holds no state of its own.
- [components/cli/](components/cli/index.md) — Argparse subparser tree, `main()` dispatch, and a sys.argv intercept for `exec` so user commands keep their flags.
- [components/workspace/](components/workspace/index.md) — `Workspace` = `(host_path, view_path, name, container, image, container_dir)`. `_resolve_target` turns a CLI target (or cwd) into a `Workspace`, consuming the workspace env vars inside a container.
- [components/base-image/](components/base-image/index.md) — Single-file distribution: the saturn-base Containerfile is inlined; build context is assembled in a temp dir with a copy of saturn itself.
- [components/mixins/](components/mixins/index.md) — Named bundles of (slot records + install snippet) for user-global state like SSH keys, gh tokens, or Claude auth. Each slot has `env`/`target`/`kind`/`default_host`.
- [components/engine/](components/engine/index.md) — Subprocess wrappers around the `docker` CLI + socket/env setup. The only module that calls subprocess.
- [boundaries/engine-socket.md](boundaries/engine-socket.md) — The bind-mounted `/var/run/docker.sock`: container-root maps to host-user under rootless userns, so no sudo is needed inside.
- [boundaries/nested-env.md](boundaries/nested-env.md) — The env contract (`SATURN_HOST_SOCK`, `SATURN_HOST_WORKSPACE`, `SATURN_WORKSPACE`, per-slot `SATURN_MIXIN_*`) that lets saturn inside saturn create siblings on the host engine.
- [decisions/](decisions/) — numbered, append-only design decisions
- [plans/](plans/) — pending work
- [experiment_journal/](experiment_journal/) — findings about external-world behavior
