# saturn

> Single-file Python CLI for rootless podman/Docker dev containers. Each project is a host directory under `$HOME/saturn/`; the engine socket and host `$HOME` are bind-mounted in. Works at any nesting level.

## Overview

saturn manages per-project dev-container lifecycles on a rootless container engine. Each project is a host directory `$HOME/saturn/<name>/` plus an optional image + container. The container runs as root; rootless userns maps that back to your host user, so files written from inside land on disk with the right ownership.

A saturn container has the `docker` CLI wired to the host engine's socket and can itself invoke `saturn` — which creates **siblings** on the host engine. `SATURN_HOST_SOCK` and `SATURN_HOST_HOME` propagate so the innermost saturn still reaches the host engine and can bind-mount from real host paths.

Implementation: one Python file, stdlib-only. All engine mutations flow through the host's docker-compat socket (`DOCKER_HOST=unix://...`) so concurrent saturn invocations are serialized by the single service process.

## Sub-documents

- [architecture.md](architecture.md) — One Python file. Logical components communicate through small helpers; all state lives in engine objects + host directories.
- [components/cli/](components/cli/index.md) — Argparse subparser tree, `main()` dispatch, and a sys.argv intercept for `exec` so user commands keep their flags.
- [components/project/](components/project/index.md) — Projects are host directories; names map mechanically to container/image names. Discovery unions host-dir listing with container-label filter.
- [components/base-image/](components/base-image/index.md) — Single-file distribution: the saturn-base Containerfile is inlined; build context is assembled in a temp dir with a copy of saturn itself.
- [components/mixins/](components/mixins/index.md) — Named bundles of (HOME-relative bind-mount paths + install snippet) for user-global state like SSH keys, gh tokens, or Claude auth.
- [components/engine/](components/engine/index.md) — Subprocess wrappers around the `docker` CLI + socket/env setup. The only module that calls subprocess.
- [boundaries/engine-socket.md](boundaries/engine-socket.md) — The bind-mounted `/var/run/docker.sock`: container-root maps to host-user under rootless userns, so no sudo is needed inside.
- [boundaries/nested-env.md](boundaries/nested-env.md) — The env contract (`SATURN_HOST_SOCK`, `SATURN_HOST_HOME`) that lets saturn inside saturn create siblings on the host engine.
- [decisions/](decisions/) — numbered, append-only design decisions
- [plans/](plans/) — pending work
- [experiment_journal/](experiment_journal/) — findings about external-world behavior
