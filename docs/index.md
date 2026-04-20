# saturn

> Single-file Python CLI for rootless podman/Docker dev containers with zero host state and nesting support.

## Overview

saturn manages per-project dev-container lifecycles on a rootless container engine. Each project is a named volume + optional image + optional container; everything is discovered by labels on volumes, so the host filesystem is never touched. Source, `.git/`, and a project-specific `.saturn/Containerfile` all live inside `saturn_ws_<name>`, committed with the project's own git.

A saturn container runs as a non-root user (`agent`, uid 10001), has the `docker` CLI wired to the host engine's socket, and can itself invoke `saturn` — which creates **siblings** on the host engine (not truly nested). The CLI auto-propagates the env vars needed for that nesting to work unchanged.

Implementation: one Python file, stdlib-only, ~500 lines. All engine mutations flow through the host's docker-compat socket (`DOCKER_HOST=unix://...`) so concurrent saturn invocations are serialized by the single service process.

## Sub-documents

- [architecture.md](architecture.md) — One Python file. Logical components communicate through small, typed helpers; all state lives in engine-managed objects (images, volumes, containers).
- [components/cli/](components/cli/index.md) — Argparse subparser tree, `main()` dispatch, and a sys.argv intercept for `exec` so user commands keep their flags.
- [components/project/](components/project/index.md) — Derives every engine resource name from a single `<name>`. Projects exist iff their ws volume exists; discovery is label-based.
- [components/base-image/](components/base-image/index.md) — Single-file distribution: the saturn-base Containerfile is inlined as a Python string; the build context is assembled in a temp dir with a copy of saturn itself.
- [components/mixins/](components/mixins/index.md) — Named bundles of (install snippet + user-global volume + target path) that carry per-user state like SSH keys, gh tokens, or Claude auth into project containers.
- [components/engine/](components/engine/index.md) — Subprocess wrappers around the `docker` CLI + socket/env setup. The only module that calls subprocess.
- [boundaries/engine-socket.md](boundaries/engine-socket.md) — The bind-mounted `/var/run/docker.sock` is a user-namespace boundary: which inside-uid can open it depends on rootless vs rootful and on sudo use.
- [boundaries/nested-env.md](boundaries/nested-env.md) — The env contract that lets saturn inside a saturn container create siblings on the host engine — distinct from the *host* shell's env, whose role is limited.
- [decisions/](decisions/) — numbered, append-only design decisions
- [plans/](plans/) — pending work (managed by `/plan-work` / `/update-project-design`)
- [experiment_journal/](experiment_journal/) — findings about external-world behavior (managed by `/log-experiment`)
